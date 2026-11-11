using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;

namespace SmcTerminal.Gateway.Services;

/// <summary>How the gateway launches the Python engine when it owns it.</summary>
public sealed record EngineProcessOptions(string Python, string WorkingDirectory, int Port, string LogFile);

/// <summary>Observable state of the supervised engine (shown in /api/health and the UI).</summary>
public sealed record EngineSupervisorStatus(bool Managed, string State, int? Pid, int Restarts, string? Failure, string? LogFile)
{
    public static readonly EngineSupervisorStatus Unmanaged = new(false, "external", null, 0, null, null);
}

/// <summary>
/// Owns the Python engine child process: starts it hidden (no console window), waits for
/// /health, restarts it with back-off when it dies, and kills the whole process tree on
/// shutdown. On Windows the child is also placed in a kill-on-close job object so it can
/// never outlive the gateway, even when the gateway itself crashes.
/// </summary>
public sealed class EngineSupervisor(EngineProcessOptions options, ILogger<EngineSupervisor> log) : BackgroundService
{
    private static readonly TimeSpan[] Backoff =
    {
        TimeSpan.FromSeconds(2), TimeSpan.FromSeconds(5), TimeSpan.FromSeconds(10), TimeSpan.FromSeconds(30), TimeSpan.FromSeconds(60),
    };

    private readonly TaskCompletionSource _ready = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private readonly object _gate = new();
    private readonly HttpClient _probe = new() { Timeout = TimeSpan.FromSeconds(2) };
    private Process? _process;
    private StreamWriter? _logWriter;
    private int _restarts;

    public static EngineSupervisorStatus Status { get; private set; } = EngineSupervisorStatus.Unmanaged;

    /// <summary>Completes the first time the engine answers /health.</summary>
    public Task Ready => _ready.Task;

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        OpenLog();
        var attempt = 0;
        while (!stoppingToken.IsCancellationRequested)
        {
            if (!File.Exists(options.Python))
            {
                Publish("missing", null, $"Python engine tidak ditemukan di {options.Python}. Jalankan setup.bat terlebih dahulu.");
                await Delay(TimeSpan.FromSeconds(30), stoppingToken);
                continue;
            }

            Process process;
            try
            {
                process = Start();
            }
            catch (Exception ex)
            {
                Publish("failed", null, $"Gagal menjalankan engine Python: {ex.Message}");
                log.LogError(ex, "Engine start failed");
                await Delay(Backoff[Math.Min(attempt++, Backoff.Length - 1)], stoppingToken);
                continue;
            }

            Publish("starting", process.Id, null);
            if (await WaitHealthy(process, TimeSpan.FromSeconds(120), stoppingToken))
            {
                attempt = 0;
                Publish("running", process.Id, null);
                log.LogInformation("Engine Python siap (pid {Pid}, port {Port})", process.Id, options.Port);
                _ready.TrySetResult();
            }

            try
            {
                await process.WaitForExitAsync(stoppingToken);
            }
            catch (OperationCanceledException)
            {
                break;
            }

            var code = SafeExitCode(process);
            Release(process);
            _restarts++;
            Publish("restarting", null, $"Engine Python berhenti (exit {code}); dijalankan ulang. Detail: {options.LogFile}");
            log.LogWarning("Engine exited with code {Code}; restarting", code);
            await Delay(Backoff[Math.Min(attempt++, Backoff.Length - 1)], stoppingToken);
        }
    }

    public override async Task StopAsync(CancellationToken cancellationToken)
    {
        await base.StopAsync(cancellationToken);
        Process? process;
        lock (_gate)
        {
            process = _process;
        }
        if (process is not null)
        {
            try
            {
                if (!process.HasExited)
                {
                    process.Kill(entireProcessTree: true);
                    process.WaitForExit(5000);
                }
            }
            catch (Exception ex) when (ex is InvalidOperationException or System.ComponentModel.Win32Exception)
            {
                log.LogWarning("Engine stop: {Message}", ex.Message);
            }
            Release(process);
        }
        Publish("stopped", null, null);
        lock (_gate)
        {
            _logWriter?.Dispose();
            _logWriter = null;
        }
    }

    private Process Start()
    {
        var psi = new ProcessStartInfo(options.Python)
        {
            WorkingDirectory = options.WorkingDirectory,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
        };
        foreach (var arg in new[] { "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", options.Port.ToString(), "--log-level", "warning" })
        {
            psi.ArgumentList.Add(arg);
        }
        psi.Environment["PYTHONUNBUFFERED"] = "1";
        psi.Environment["PYTHONIOENCODING"] = "utf-8";

        var process = new Process { StartInfo = psi, EnableRaisingEvents = true };
        process.OutputDataReceived += (_, e) => WriteLog(e.Data);
        process.ErrorDataReceived += (_, e) => WriteLog(e.Data);
        process.Start();
        lock (_gate)
        {
            _process = process;
        }
        KillOnCloseJob.TryAssign(process, log);
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();
        WriteLog($"--- engine started pid {process.Id} port {options.Port} ---");
        return process;
    }

    private async Task<bool> WaitHealthy(Process process, TimeSpan timeout, CancellationToken ct)
    {
        var deadline = DateTime.UtcNow + timeout;
        var url = $"http://127.0.0.1:{options.Port}/health";
        while (DateTime.UtcNow < deadline && !ct.IsCancellationRequested && !process.HasExited)
        {
            try
            {
                using var response = await _probe.GetAsync(url, ct);
                if (response.IsSuccessStatusCode)
                {
                    return true;
                }
            }
            catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException && !ct.IsCancellationRequested)
            {
                // still booting
            }
            await Delay(TimeSpan.FromMilliseconds(500), ct);
        }
        return false;
    }

    private void Release(Process process)
    {
        lock (_gate)
        {
            if (ReferenceEquals(_process, process))
            {
                _process = null;
            }
        }
        process.Dispose();
    }

    private void Publish(string state, int? pid, string? failure) =>
        Status = new EngineSupervisorStatus(true, state, pid, _restarts, failure, options.LogFile);

    private void OpenLog()
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(options.LogFile)!);
            var info = new FileInfo(options.LogFile);
            if (info.Exists && info.Length > 5_000_000)
            {
                File.Move(options.LogFile, options.LogFile + ".old", overwrite: true);
            }
            lock (_gate)
            {
                _logWriter = new StreamWriter(new FileStream(options.LogFile, FileMode.Append, FileAccess.Write, FileShare.ReadWrite), Encoding.UTF8)
                {
                    AutoFlush = true,
                };
            }
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            log.LogWarning("Engine log unavailable: {Message}", ex.Message);
        }
    }

    private void WriteLog(string? line)
    {
        if (line is null)
        {
            return;
        }
        lock (_gate)
        {
            _logWriter?.WriteLine($"{DateTimeOffset.Now:yyyy-MM-dd HH:mm:ss} {line}");
        }
    }

    private static int? SafeExitCode(Process process)
    {
        try
        {
            return process.ExitCode;
        }
        catch (InvalidOperationException)
        {
            return null;
        }
    }

    private static async Task Delay(TimeSpan delay, CancellationToken ct)
    {
        try
        {
            await Task.Delay(delay, ct);
        }
        catch (OperationCanceledException)
        {
            // stopping
        }
    }
}

/// <summary>Windows job object that terminates assigned processes when the gateway process ends.</summary>
internal static class KillOnCloseJob
{
    private static readonly Lazy<IntPtr> Job = new(Create);

    public static void TryAssign(Process process, ILogger log)
    {
        if (!OperatingSystem.IsWindows())
        {
            return;
        }
        try
        {
            if (Job.Value == IntPtr.Zero || !AssignProcessToJobObject(Job.Value, process.Handle))
            {
                log.LogDebug("Could not place engine in job object (error {Error})", Marshal.GetLastWin32Error());
            }
        }
        catch (Exception ex) when (ex is InvalidOperationException or System.ComponentModel.Win32Exception)
        {
            log.LogDebug("Job object assignment failed: {Message}", ex.Message);
        }
    }

    private static IntPtr Create()
    {
        var job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero)
        {
            return IntPtr.Zero;
        }
        var info = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        {
            BasicLimitInformation = new JOBOBJECT_BASIC_LIMIT_INFORMATION { LimitFlags = 0x2000 }, // KILL_ON_JOB_CLOSE
        };
        var length = Marshal.SizeOf<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>();
        var ptr = Marshal.AllocHGlobal(length);
        try
        {
            Marshal.StructureToPtr(info, ptr, false);
            SetInformationJobObject(job, 9 /* JobObjectExtendedLimitInformation */, ptr, (uint)length);
        }
        finally
        {
            Marshal.FreeHGlobal(ptr);
        }
        return job;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_BASIC_LIMIT_INFORMATION
    {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct IO_COUNTERS
    {
        public ulong ReadOperationCount;
        public ulong WriteOperationCount;
        public ulong OtherOperationCount;
        public ulong ReadTransferCount;
        public ulong WriteTransferCount;
        public ulong OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    {
        public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
        public IO_COUNTERS IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateJobObject(IntPtr attributes, string? name);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(IntPtr job, int infoClass, IntPtr info, uint length);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
}
