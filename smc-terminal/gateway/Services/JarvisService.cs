using System.ComponentModel;
using System.Diagnostics;
using System.IO.Compression;
using System.Net;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace SmcTerminal.Gateway.Services;

/// <summary>Where the JARVIS tab keeps Mark LVII (FatihMakes, CC BY-NC 4.0) and where its dashboard answers.</summary>
public sealed record JarvisOptions(string Root, string LogFile, Uri Archive, string? Python, int DashboardPort, string? PluginSource = null)
{
    public string AppDirectory => Path.Combine(Root, "app");
    public string VenvDirectory => Path.Combine(Root, "venv");
    public string ManifestFile => Path.Combine(Root, "installed.json");
    public string StagingDirectory => Path.Combine(Root, "app.new");
    /// <summary>PC → phone: where Mark LVII finds the SHARX phone_command plugin and the paired phone.</summary>
    public string PhonePlugin => Path.Combine(AppDirectory, "plugins", "sharx_phone.py");
    public string PhoneConfig => Path.Combine(AppDirectory, "config", "sharx_phone.json");
    public string VenvPython => OperatingSystem.IsWindows()
        ? Path.Combine(VenvDirectory, "Scripts", "python.exe")
        : Path.Combine(VenvDirectory, "bin", "python");
}

/// <summary>What the last install put on disk (installed.json). <c>Files</c> are the archive's files, relative to the app folder.</summary>
public sealed record JarvisInstall(string? Commit, DateTimeOffset InstalledAt, string Python, string PythonVersion, string Archive, bool SetupOk, IReadOnlyList<string> Files);

public sealed record JarvisInstallView(string? Commit, DateTimeOffset InstalledAt, string PythonVersion, bool SetupOk);

/// <summary>The install job: State running | done | failed | canceled; Step python | download | extract | venv | setup | check.</summary>
public sealed record JarvisJobState(string State, string Step, DateTimeOffset Started, DateTimeOffset? Finished, string? Error);

/// <summary>Mark LVII started by this app: State stopped | running | stopping | exited.</summary>
public sealed record JarvisProcessState(string State, int? Pid, DateTimeOffset? Started, int? ExitCode, DateTimeOffset? Exited);

/// <summary>The dashboard on this PC: State online (HTTP, can be shown in the tab) | https (TLS on, self-signed) | offline | other
/// (the port answers but it is not Mark LVII).</summary>
public sealed record JarvisDashboardState(string State, string? Url, string? LanUrl, string? Detail, DateTimeOffset Checked);

/// <summary>PC → phone: the phone this PC sends requests to (JARVIS on the phone in SHARX Android).</summary>
public sealed record JarvisPhoneState(bool Paired, string? Address, DateTimeOffset? PairedAt, bool PluginInstalled);

/// <summary>Everything the JARVIS tab shows. <c>Log</c> holds the output lines after the caller's <c>after</c> sequence
/// number (at most 150); <c>LogSeq</c> is the number of the last line.</summary>
public sealed record JarvisStatus(
    string Mode, string Source, string License, string LicenseUrl, string Folder, bool Installed, JarvisInstallView? Install,
    JarvisJobState? Job, JarvisProcessState Process, JarvisDashboardState Dashboard, JarvisPhoneState Phone, long LogSeq,
    IReadOnlyList<string> Log);

public sealed record JarvisResult(bool Ok, string? Error, string Message);

/// <summary>
/// Installs Mark LVII for the JARVIS tab (GitHub archive → its own virtualenv → its setup.py), starts and stops its
/// main.py, and probes its dashboard. Mark LVII stays a separate program: its code is downloaded from its repository and
/// is not part of SHARX. The process started here is owned by the gateway: it is placed in the same kill-on-close job as
/// the engine and is closed when the app closes.
/// </summary>
public sealed partial class JarvisService(JarvisOptions options, IHttpClientFactory http, ILogger<JarvisService> log) : IHostedService, IDisposable
{
    public const string HttpName = "jarvis";
    public const string Source = "https://github.com/FatihMakes/Mark-LVII";
    public const string License = "CC BY-NC 4.0";
    public const string LicenseUrl = "https://creativecommons.org/licenses/by-nc/4.0/";

    private const long MaxArchiveBytes = 100L << 20;   // the repository archive is ~1 MB
    private const long MaxExtractedBytes = 400L << 20;
    private const int MaxEntries = 20_000;
    private const int KeptLines = 400;
    private const int ShownLines = 150;
    private static readonly StringComparison PathComparison = OperatingSystem.IsWindows() ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;

    private readonly object _gate = new();
    private readonly LinkedList<(long Seq, string Text)> _lines = new();
    private readonly CancellationTokenSource _stopping = new();
    private readonly SemaphoreSlim _probeLock = new(1, 1);
    private readonly HttpClient _probe = new(new SocketsHttpHandler
    {
        AllowAutoRedirect = false, UseProxy = false, ConnectTimeout = TimeSpan.FromSeconds(1.5),
    }) { Timeout = TimeSpan.FromSeconds(3) };
    private StreamWriter? _logWriter;
    private JarvisJobState? _job;
    private CancellationTokenSource? _jobCts;
    private Task _jobTask = Task.CompletedTask;
    private Process? _process;
    private JarvisProcessState _processState = new("stopped", null, null, null, null);
    private JarvisDashboardState? _dashboard;
    private (DateTime Stamp, JarvisInstall? Value) _manifest;
    private long _lineSeq;

    private sealed record PythonInfo(string Path, int Major, int Minor, string Version);

    private sealed class JarvisException(string message) : Exception(message);

    // ------------------------------------------------------------------ status

    public async Task<JarvisStatus> StatusAsync(long after, CancellationToken ct)
    {
        var dashboard = await ProbeAsync(fresh: false, ct);
        var install = ReadManifest();
        lock (_gate)
        {
            var view = install is null ? null : new JarvisInstallView(install.Commit, install.InstalledAt, install.PythonVersion, install.SetupOk);
            var lines = _lines.Where(l => l.Seq > after).Select(l => l.Text).ToList();
            return new JarvisStatus("local", Source, License, LicenseUrl, options.AppDirectory, IsInstalled(install), view, _job,
                _processState, dashboard, PhoneState(), _lineSeq, lines.Count > ShownLines ? lines[^ShownLines..] : lines);
        }
    }

    private bool IsInstalled(JarvisInstall? install) =>
        install is { SetupOk: true } && File.Exists(options.VenvPython) && File.Exists(Path.Combine(options.AppDirectory, "main.py"));

    // ------------------------------------------------------------------ install

    public async Task<JarvisResult> BeginInstallAsync(CancellationToken ct)
    {
        var dashboard = await ProbeAsync(fresh: true, ct);
        lock (_gate)
        {
            if (!_jobTask.IsCompleted)
            {
                return Fail("busy", "Pemasangan JARVIS sedang berjalan.");
            }
            if (_process is not null)
            {
                return Fail("running", "Hentikan JARVIS dulu, lalu pasang ulang / perbarui.");
            }
            if (dashboard.State is "online" or "https")
            {
                return Fail("external", "JARVIS lain sedang berjalan di PC ini (dashboard aktif). Tutup jendela JARVIS itu dulu.");
            }
            _jobCts?.Dispose();
            _jobCts = CancellationTokenSource.CreateLinkedTokenSource(_stopping.Token);
            _job = new JarvisJobState("running", "python", DateTimeOffset.Now, null, null);
            var token = _jobCts.Token;
            _jobTask = Task.Run(() => InstallAsync(token), CancellationToken.None);
        }
        return new JarvisResult(true, null, "Pemasangan JARVIS dimulai.");
    }

    public JarvisResult CancelInstall()
    {
        lock (_gate)
        {
            if (_jobTask.IsCompleted || _jobCts is null)
            {
                return Fail("idle", "Tidak ada pemasangan yang berjalan.");
            }
            _jobCts.Cancel();
        }
        return new JarvisResult(true, null, "Pemasangan dibatalkan.");
    }

    private async Task InstallAsync(CancellationToken ct)
    {
        try
        {
            Directory.CreateDirectory(options.Root);
            Step("python", "Mencari Python 3.11–3.13 …");
            var python = await FindPythonAsync(ct) ?? throw new JarvisException(
                "Python 3.11–3.13 tidak ditemukan. Pasang Python 3.12 dari https://www.python.org/downloads/ " +
                "(centang \"Add python.exe to PATH\"), lalu tekan PASANG lagi.");
            Line($"Python {python.Version}: {python.Path}");

            Step("download", $"Mengunduh Mark LVII dari {options.Archive} …");
            var zip = Path.Combine(options.Root, "download.zip");
            await DownloadAsync(zip, ct);

            Step("extract", "Membuka arsip …");
            var (commit, files) = Extract(zip, ct);
            File.Delete(zip);
            Line($"{files.Count} file Mark LVII{(commit is null ? "" : $" (commit {commit[..7]})")} di {options.AppDirectory}");
            // written before setup.py: a later install still knows which files came from the archive
            WriteManifest(new JarvisInstall(commit, DateTimeOffset.Now, python.Path, python.Version, options.Archive.ToString(), false, files));

            EnsurePlugin();

            Step("venv", "Menyiapkan virtualenv Python khusus JARVIS …");
            await EnsureVenvAsync(python, ct);

            Step("setup", "Menjalankan setup.py Mark LVII: dependensi Python + browser Playwright (5–20 menit, ±1–2 GB) …");
            var code = await RunToolAsync(options.VenvPython, new[] { "setup.py" }, options.AppDirectory, ct);
            if (code != 0)
            {
                throw new JarvisException($"setup.py gagal (exit {code}). Lihat log; periksa koneksi internet lalu tekan PASANG lagi.");
            }

            Step("check", "Memeriksa modul JARVIS dan dashboard …");
            code = await RunToolAsync(options.VenvPython,
                new[] { "-c", "import fastapi, uvicorn, cryptography, PyQt6, google.genai; print('modul JARVIS dan dashboard lengkap')" },
                options.AppDirectory, ct);
            if (code != 0)
            {
                throw new JarvisException("Modul JARVIS belum lengkap setelah setup.py (lihat log). Tekan PASANG lagi.");
            }
            WriteManifest(new JarvisInstall(commit, DateTimeOffset.Now, python.Path, python.Version, options.Archive.ToString(), true, files));
            Finish("done", null);
        }
        catch (OperationCanceledException)
        {
            Finish("canceled", "Pemasangan dibatalkan.");
        }
        catch (JarvisException ex)
        {
            Finish("failed", ex.Message);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or HttpRequestException or InvalidDataException or Win32Exception)
        {
            log.LogWarning(ex, "JARVIS install failed");
            Finish("failed", $"Pemasangan gagal: {ex.Message}");
        }
    }

    private void Step(string step, string text)
    {
        lock (_gate)
        {
            if (_job is not null)
            {
                _job = _job with { Step = step };
            }
        }
        Line($"▶ {text}");
    }

    private void Finish(string state, string? error)
    {
        lock (_gate)
        {
            if (_job is not null)
            {
                _job = _job with { State = state, Finished = DateTimeOffset.Now, Error = error };
            }
        }
        Line(state == "done" ? "✔ JARVIS terpasang. Tekan JALANKAN." : $"✖ {error}");
    }

    private async Task<PythonInfo?> FindPythonAsync(CancellationToken ct)
    {
        PythonInfo? newer = null;
        foreach (var (exe, args) in PythonCandidates())
        {
            var info = await ProbePythonAsync(exe, args, ct);
            if (info is null || info.Major != 3)
            {
                continue;
            }
            if (info.Minor is >= 11 and <= 13)
            {
                return info;
            }
            if (info.Minor > 13)
            {
                newer ??= info;
            }
        }
        if (newer is not null)
        {
            // setup.py itself continues on a newer Python; a package without a wheel for it fails there with pip's reason
            Line($"Hanya Python {newer.Version} yang ditemukan; Mark LVII diuji sampai 3.13. Dicoba tetap.");
        }
        return newer;
    }

    private IEnumerable<(string Exe, string[] Args)> PythonCandidates()
    {
        if (!string.IsNullOrWhiteSpace(options.Python))
        {
            yield return (options.Python, Array.Empty<string>());
        }
        if (OperatingSystem.IsWindows())
        {
            foreach (var version in new[] { "3.12", "3.13", "3.11" })
            {
                yield return ("py", new[] { $"-{version}" });
            }
            yield return ("python", Array.Empty<string>());
            yield return ("python3", Array.Empty<string>());
        }
        else
        {
            foreach (var version in new[] { "3.12", "3.13", "3.11" })
            {
                yield return ($"python{version}", Array.Empty<string>());
            }
            yield return ("python3", Array.Empty<string>());
        }
    }

    private async Task<PythonInfo?> ProbePythonAsync(string exe, IEnumerable<string> args, CancellationToken ct)
    {
        var psi = StartInfo(exe, args.Concat(new[] { "-c", "import sys;print('%d %d %d' % sys.version_info[:3]);print(sys.executable)" }), options.Root);
        using var process = new Process { StartInfo = psi };
        try
        {
            if (!process.Start())
            {
                return null;
            }
        }
        catch (Win32Exception)
        {
            return null; // not installed
        }
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeout.CancelAfter(TimeSpan.FromSeconds(20));
        try
        {
            var errors = process.StandardError.ReadToEndAsync(timeout.Token); // drained so a chatty stderr cannot block
            var output = await process.StandardOutput.ReadToEndAsync(timeout.Token);
            await process.WaitForExitAsync(timeout.Token);
            await errors;
            if (process.ExitCode != 0)
            {
                return null;
            }
            var lines = output.Split('\n', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
            var parts = lines.Length >= 2 ? lines[0].Split(' ') : Array.Empty<string>();
            if (parts.Length != 3 || !int.TryParse(parts[0], out var major) || !int.TryParse(parts[1], out var minor) || !File.Exists(lines[1]))
            {
                return null;
            }
            return new PythonInfo(lines[1], major, minor, $"{parts[0]}.{parts[1]}.{parts[2]}");
        }
        catch (OperationCanceledException) when (!ct.IsCancellationRequested)
        {
            Kill(process);
            return null;
        }
        catch (OperationCanceledException)
        {
            Kill(process);
            throw;
        }
    }

    private async Task DownloadAsync(string target, CancellationToken ct)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeout.CancelAfter(TimeSpan.FromMinutes(10));
        var client = http.CreateClient(HttpName);
        using var response = await client.GetAsync(options.Archive, HttpCompletionOption.ResponseHeadersRead, timeout.Token);
        if (!response.IsSuccessStatusCode)
        {
            throw new JarvisException($"Unduhan Mark LVII gagal: HTTP {(int)response.StatusCode} dari {response.RequestMessage?.RequestUri?.Host}.");
        }
        if (response.Content.Headers.ContentLength > MaxArchiveBytes)
        {
            throw new JarvisException("Arsip Mark LVII lebih besar dari 100 MB; unduhan dihentikan.");
        }
        var part = target + ".part";
        long done = 0;
        await using (var source = await response.Content.ReadAsStreamAsync(timeout.Token))
        await using (var file = File.Create(part))
        {
            var buffer = new byte[81920];
            var report = 1L << 20;
            int read;
            while ((read = await source.ReadAsync(buffer, timeout.Token)) > 0)
            {
                done += read;
                if (done > MaxArchiveBytes)
                {
                    throw new JarvisException("Arsip Mark LVII lebih besar dari 100 MB; unduhan dihentikan.");
                }
                await file.WriteAsync(buffer.AsMemory(0, read), timeout.Token);
                if (done >= report)
                {
                    Line($"  {done / 1048576.0:0.0} MB");
                    report += 1L << 20;
                }
            }
        }
        File.Move(part, target, overwrite: true);
        Line($"Terunduh {done / 1024.0:0} KB.");
    }

    /// <summary>
    /// Unpacks the archive into a staging folder, then moves its files over the app folder. Files the user or Mark LVII
    /// created there (config/api_keys.json, memory, plugins, downloads) are not in the archive and stay; files a previous
    /// archive had and this one no longer has are removed.
    /// </summary>
    private (string? Commit, List<string> Files) Extract(string zipPath, CancellationToken ct)
    {
        var staging = options.StagingDirectory;
        if (Directory.Exists(staging))
        {
            Directory.Delete(staging, recursive: true);
        }
        Directory.CreateDirectory(staging);
        var stagingRoot = Path.GetFullPath(staging) + Path.DirectorySeparatorChar;
        var files = new List<string>();
        using (var archive = ZipFile.OpenRead(zipPath))
        {
            if (archive.Entries.Count is 0 or > MaxEntries)
            {
                throw new JarvisException("Arsip Mark LVII tidak valid.");
            }
            var first = archive.Entries[0].FullName;
            var slash = first.IndexOf('/');
            if (slash <= 0)
            {
                throw new JarvisException("Arsip Mark LVII tidak berisi folder proyek.");
            }
            var prefix = first[..(slash + 1)];
            long total = 0;
            foreach (var entry in archive.Entries)
            {
                ct.ThrowIfCancellationRequested();
                if (!entry.FullName.StartsWith(prefix, StringComparison.Ordinal))
                {
                    throw new JarvisException("Arsip Mark LVII berisi file di luar folder proyek.");
                }
                var relative = entry.FullName[prefix.Length..];
                if (relative.Length == 0 || relative.EndsWith('/'))
                {
                    continue; // folder entry
                }
                var target = SafePath(stagingRoot, relative) ?? throw new JarvisException($"Arsip Mark LVII berisi path tidak aman: {relative}");
                total += entry.Length;
                if (total > MaxExtractedBytes)
                {
                    throw new JarvisException("Isi arsip Mark LVII terlalu besar.");
                }
                Directory.CreateDirectory(Path.GetDirectoryName(target)!);
                entry.ExtractToFile(target, overwrite: true);
                files.Add(relative);
            }
        }
        foreach (var required in new[] { "main.py", "setup.py", "requirements.txt" })
        {
            if (!files.Contains(required))
            {
                throw new JarvisException($"Arsip ini bukan Mark LVII ({required} tidak ada).");
            }
        }
        var commit = ZipComment(zipPath);

        var appRoot = Path.GetFullPath(options.AppDirectory) + Path.DirectorySeparatorChar;
        var previous = ReadManifest()?.Files ?? Array.Empty<string>();
        Directory.CreateDirectory(options.AppDirectory);
        foreach (var relative in files)
        {
            var target = SafePath(appRoot, relative)!;
            Directory.CreateDirectory(Path.GetDirectoryName(target)!);
            File.Move(SafePath(stagingRoot, relative)!, target, overwrite: true);
        }
        var current = new HashSet<string>(files, StringComparer.Ordinal);
        foreach (var stale in previous.Where(f => !current.Contains(f)))
        {
            var path = SafePath(appRoot, stale);
            if (path is not null && File.Exists(path))
            {
                File.Delete(path);
                Line($"  dihapus (tidak ada lagi di Mark LVII): {stale}");
            }
        }
        Directory.Delete(staging, recursive: true);
        return (commit, files);
    }

    /// <summary>The full path of an archive-relative path inside <paramref name="root"/>, or null when it would leave it.</summary>
    private static string? SafePath(string root, string relative)
    {
        if (relative.Length == 0 || relative.Contains('\0') || Path.IsPathRooted(relative) || relative.Contains(':'))
        {
            return null;
        }
        var full = Path.GetFullPath(Path.Combine(root, relative.Replace('/', Path.DirectorySeparatorChar)));
        return full.StartsWith(root, PathComparison) ? full : null;
    }

    /// <summary>GitHub writes the commit of an archive into the zip comment (end-of-central-directory record).</summary>
    private static string? ZipComment(string path)
    {
        using var stream = File.OpenRead(path);
        var length = (int)Math.Min(stream.Length, 22 + 65535);
        var buffer = new byte[length];
        stream.Seek(-length, SeekOrigin.End);
        stream.ReadExactly(buffer);
        for (var i = length - 22; i >= 0; i--)
        {
            if (buffer[i] != 0x50 || buffer[i + 1] != 0x4b || buffer[i + 2] != 5 || buffer[i + 3] != 6)
            {
                continue;
            }
            var size = buffer[i + 20] | (buffer[i + 21] << 8);
            if (i + 22 + size > length)
            {
                return null;
            }
            var comment = Encoding.ASCII.GetString(buffer, i + 22, size).Trim();
            return comment.Length == 40 && comment.All(Uri.IsHexDigit) ? comment.ToLowerInvariant() : null;
        }
        return null;
    }

    private async Task EnsureVenvAsync(PythonInfo python, CancellationToken ct)
    {
        var current = File.Exists(options.VenvPython) ? await ProbePythonAsync(options.VenvPython, Array.Empty<string>(), ct) : null;
        if (current is not null && current.Major == python.Major && current.Minor == python.Minor)
        {
            Line($"virtualenv Python {current.Version} dipakai lagi.");
            return;
        }
        if (Directory.Exists(options.VenvDirectory))
        {
            Line("virtualenv lama memakai Python lain; dibuat ulang …");
            Directory.Delete(options.VenvDirectory, recursive: true);
        }
        var code = await RunToolAsync(python.Path, new[] { "-m", "venv", options.VenvDirectory }, options.Root, ct);
        if (code != 0 || !File.Exists(options.VenvPython))
        {
            throw new JarvisException("Gagal membuat virtualenv Python untuk JARVIS (lihat log).");
        }
        // newer pip resolves wheels of current packages; a failure here is not fatal, setup.py reports real problems
        await RunToolAsync(options.VenvPython, new[] { "-m", "pip", "install", "--upgrade", "pip" }, options.Root, ct);
    }

    /// <summary>Runs one install step with its output in the log; cancelling kills its whole process tree.</summary>
    private async Task<int> RunToolAsync(string exe, IEnumerable<string> args, string workingDirectory, CancellationToken ct)
    {
        using var process = new Process { StartInfo = StartInfo(exe, args, workingDirectory) };
        process.OutputDataReceived += (_, e) => Line(e.Data);
        process.ErrorDataReceived += (_, e) => Line(e.Data);
        try
        {
            process.Start();
        }
        catch (Win32Exception ex)
        {
            throw new JarvisException($"Tidak bisa menjalankan {Path.GetFileName(exe)}: {ex.Message}");
        }
        KillOnCloseJob.TryAssign(process, log);
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();
        try
        {
            await process.WaitForExitAsync(ct);
            return process.ExitCode;
        }
        catch (OperationCanceledException)
        {
            Kill(process);
            throw;
        }
    }

    // ------------------------------------------------------------------ run

    public async Task<JarvisResult> LaunchAsync(CancellationToken ct)
    {
        var dashboard = await ProbeAsync(fresh: true, ct);
        var install = ReadManifest();
        lock (_gate)
        {
            if (!_jobTask.IsCompleted)
            {
                return Fail("busy", "Tunggu pemasangan JARVIS selesai.");
            }
            if (_process is not null)
            {
                return Fail("running", "JARVIS sudah berjalan.");
            }
            if (!IsInstalled(install))
            {
                return Fail("not_installed", "JARVIS belum terpasang. Tekan PASANG dulu.");
            }
            if (dashboard.State is "online" or "https")
            {
                return Fail("external", "JARVIS lain sudah berjalan di PC ini (dashboard aktif). Panelnya tampil di tab ini.");
            }
            if (dashboard.State == "other")
            {
                return Fail("port_busy", $"Port {options.DashboardPort} dipakai program lain, jadi dashboard JARVIS tidak bisa jalan. Tutup program itu dulu.");
            }
            var process = new Process { StartInfo = StartInfo(options.VenvPython, new[] { "main.py" }, options.AppDirectory), EnableRaisingEvents = true };
            process.OutputDataReceived += (_, e) => Line(e.Data);
            process.ErrorDataReceived += (_, e) => Line(e.Data);
            process.Exited += (_, _) => OnExited(process);
            try
            {
                process.Start();
            }
            catch (Win32Exception ex)
            {
                process.Dispose();
                return Fail("start_failed", $"JARVIS gagal dijalankan: {ex.Message}");
            }
            KillOnCloseJob.TryAssign(process, log);
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
            _process = process;
            _processState = new JarvisProcessState("running", process.Id, DateTimeOffset.Now, null, null);
            _dashboard = null;
            Line($"--- JARVIS dijalankan (pid {process.Id}) ---");
        }
        return new JarvisResult(true, null, "JARVIS dijalankan. Jendela HUD-nya terbuka sebentar lagi.");
    }

    public async Task<JarvisResult> StopJarvisAsync(TimeSpan grace)
    {
        Process? process;
        lock (_gate)
        {
            process = _process;
            if (process is null)
            {
                return Fail("not_running", "JARVIS tidak sedang dijalankan dari SHARX.");
            }
            _processState = _processState with { State = "stopping" };
        }
        Line("--- menutup JARVIS ---");
        var closed = false;
        try
        {
            // the HUD window closes normally first, so Mark LVII can save its session; the tree is killed after the grace
            closed = OperatingSystem.IsWindows() && process.CloseMainWindow();
        }
        catch (InvalidOperationException)
        {
            // already exited
        }
        if (!closed || !await WaitExitAsync(process, grace))
        {
            Kill(process);
            await WaitExitAsync(process, TimeSpan.FromSeconds(5));
        }
        lock (_gate)
        {
            _dashboard = null;
        }
        return new JarvisResult(true, null, "JARVIS ditutup.");
    }

    private void OnExited(Process process)
    {
        int? code = null;
        try
        {
            code = process.ExitCode;
        }
        catch (InvalidOperationException)
        {
        }
        lock (_gate)
        {
            if (!ReferenceEquals(_process, process))
            {
                return;
            }
            _process = null;
            _processState = new JarvisProcessState("exited", null, _processState.Started, code, DateTimeOffset.Now);
            _dashboard = null;
        }
        Line($"--- JARVIS berhenti (exit {code?.ToString() ?? "?"}) ---");
        // the last output lines may still be on their way; the handle is released after them
        _ = Task.Delay(TimeSpan.FromSeconds(3)).ContinueWith(_ => process.Dispose(), TaskScheduler.Default);
    }

    private static async Task<bool> WaitExitAsync(Process process, TimeSpan timeout)
    {
        using var cts = new CancellationTokenSource(timeout);
        try
        {
            await process.WaitForExitAsync(cts.Token);
            return true;
        }
        catch (OperationCanceledException)
        {
            return false;
        }
        catch (InvalidOperationException)
        {
            return true;
        }
    }

    private void Kill(Process process)
    {
        try
        {
            if (!process.HasExited)
            {
                process.Kill(entireProcessTree: true);
            }
        }
        catch (Exception ex) when (ex is InvalidOperationException or Win32Exception or NotSupportedException)
        {
            log.LogDebug("JARVIS kill: {Message}", ex.Message);
        }
    }

    private static ProcessStartInfo StartInfo(string exe, IEnumerable<string> args, string workingDirectory)
    {
        var psi = new ProcessStartInfo(exe)
        {
            WorkingDirectory = workingDirectory,
            UseShellExecute = false,
            CreateNoWindow = true, // no console window; the HUD of Mark LVII is a normal window
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
        };
        foreach (var arg in args)
        {
            psi.ArgumentList.Add(arg);
        }
        // Mark LVII and setup.py print emoji: a redirected stream on Windows would otherwise use the ANSI code page
        psi.Environment["PYTHONIOENCODING"] = "utf-8";
        psi.Environment["PYTHONUNBUFFERED"] = "1";
        psi.Environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1";
        psi.Environment["PIP_NO_INPUT"] = "1";
        return psi;
    }

    // ------------------------------------------------------------------ dashboard

    /// <summary>Asks the dashboard port whether Mark LVII answers there (cached for 1.5 s unless <paramref name="fresh"/>).</summary>
    public async Task<JarvisDashboardState> ProbeAsync(bool fresh, CancellationToken ct)
    {
        var cached = _dashboard;
        if (!fresh && cached is not null && DateTimeOffset.Now - cached.Checked < TimeSpan.FromSeconds(1.5))
        {
            return cached;
        }
        await _probeLock.WaitAsync(ct);
        try
        {
            var port = options.DashboardPort;
            var url = $"http://127.0.0.1:{port}/";
            string state;
            string? detail = null;
            string? shown = null;
            try
            {
                using var response = await _probe.GetAsync(url + "login", HttpCompletionOption.ResponseHeadersRead, ct);
                var code = (int)response.StatusCode;
                if (code is >= 300 and < 400 && response.Headers.Location is { IsAbsoluteUri: true, Scheme: "https" } location)
                {
                    state = "https";
                    shown = location.GetLeftPart(UriPartial.Authority) + "/";
                    detail = "Dashboard memakai HTTPS (sertifikat buatan sendiri), jadi tidak bisa tampil di tab ini.";
                }
                else if (response.IsSuccessStatusCode && (await ReadPrefixAsync(response, 65536, ct)).Contains("JARVIS", StringComparison.OrdinalIgnoreCase))
                {
                    state = "online";
                    shown = url;
                }
                else
                {
                    state = "other";
                    detail = $"Port {port} menjawab HTTP {code}, bukan dashboard JARVIS.";
                }
            }
            catch (HttpRequestException)
            {
                state = "offline";
            }
            catch (TaskCanceledException) when (!ct.IsCancellationRequested)
            {
                state = "offline";
                detail = "Dashboard tidak menjawab dalam 3 detik.";
            }
            var lan = LanAddress();
            var result = new JarvisDashboardState(state, shown, lan is null ? null : $"http://{lan}:{port}/", detail, DateTimeOffset.Now);
            lock (_gate)
            {
                _dashboard = result;
            }
            return result;
        }
        finally
        {
            _probeLock.Release();
        }
    }

    private static async Task<string> ReadPrefixAsync(HttpResponseMessage response, int limit, CancellationToken ct)
    {
        await using var stream = await response.Content.ReadAsStreamAsync(ct);
        var buffer = new byte[limit];
        var total = 0;
        int read;
        while (total < limit && (read = await stream.ReadAsync(buffer.AsMemory(total, limit - total), ct)) > 0)
        {
            total += read;
        }
        return Encoding.UTF8.GetString(buffer, 0, total);
    }

    /// <summary>The PC's address on the local network, for the phone. Connecting a UDP socket sends no packet: the
    /// system only picks the outgoing interface. Without a route, the first private IPv4 address of an active adapter.</summary>
    private static string? LanAddress()
    {
        try
        {
            using var socket = new Socket(AddressFamily.InterNetwork, SocketType.Dgram, ProtocolType.Udp);
            socket.Connect(new IPEndPoint(IPAddress.Parse("8.8.8.8"), 53));
            if (socket.LocalEndPoint is IPEndPoint endpoint && !IPAddress.IsLoopback(endpoint.Address) && !endpoint.Address.Equals(IPAddress.Any))
            {
                return endpoint.Address.ToString();
            }
        }
        catch (SocketException)
        {
            // no default route
        }
        try
        {
            return NetworkInterface.GetAllNetworkInterfaces()
                .Where(n => n.OperationalStatus == OperationalStatus.Up && n.NetworkInterfaceType != NetworkInterfaceType.Loopback)
                .SelectMany(n => n.GetIPProperties().UnicastAddresses)
                .Select(a => a.Address)
                .FirstOrDefault(a => a.AddressFamily == AddressFamily.InterNetwork && IsPrivate(a))?.ToString();
        }
        catch (NetworkInformationException)
        {
            return null;
        }
    }

    private static bool IsPrivate(IPAddress address)
    {
        var b = address.GetAddressBytes();
        return b[0] == 10 || (b[0] == 172 && b[1] is >= 16 and <= 31) || (b[0] == 192 && b[1] == 168);
    }

    // ------------------------------------------------------------------ PC → phone

    /// <summary>config/sharx_phone.json, read by the phone_command plugin (lower-case keys: address, token, pairedAt).</summary>
    private sealed record PhoneConfigFile(string Address, string Token, DateTimeOffset PairedAt);

    private static readonly JsonSerializerOptions PhoneJson = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase };

    private PhoneConfigFile? ReadPhone()
    {
        try
        {
            return File.Exists(options.PhoneConfig) ? JsonSerializer.Deserialize<PhoneConfigFile>(File.ReadAllText(options.PhoneConfig), PhoneJson) : null;
        }
        catch (Exception ex) when (ex is IOException or JsonException or UnauthorizedAccessException)
        {
            return null;
        }
    }

    private JarvisPhoneState PhoneState()
    {
        var phone = ReadPhone();
        return new JarvisPhoneState(phone is not null, phone?.Address, phone?.PairedAt, File.Exists(options.PhonePlugin));
    }

    /// <summary>Puts the SHARX phone_command plugin into Mark LVII's plugins folder (Mark LVII loads it at start).</summary>
    private void EnsurePlugin()
    {
        if (options.PluginSource is null || !File.Exists(options.PluginSource) || !Directory.Exists(options.AppDirectory))
        {
            return;
        }
        try
        {
            var text = File.ReadAllText(options.PluginSource);
            if (!File.Exists(options.PhonePlugin) || File.ReadAllText(options.PhonePlugin) != text)
            {
                Directory.CreateDirectory(Path.GetDirectoryName(options.PhonePlugin)!);
                File.WriteAllText(options.PhonePlugin, text);
                Line("plugin SHARX phone_command dipasang ke Mark LVII (JARVIS di PC bisa menyuruh HP).");
            }
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            log.LogWarning("JARVIS phone plugin not written: {Message}", ex.Message);
        }
    }

    /// <summary>The phone's address, only on this PC or a private network (home LAN, link-local, a VPN such as Tailscale).</summary>
    private static string ParsePhoneAddress(string? address)
    {
        var text = (address ?? "").Trim();
        foreach (var prefix in new[] { "http://", "https://" })
        {
            if (text.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
            {
                text = text[prefix.Length..];
            }
        }
        text = text.Split('/')[0];
        var colon = text.LastIndexOf(':');
        var host = colon > 0 ? text[..colon] : text;
        var port = 8010;
        if (colon > 0 && (!int.TryParse(text[(colon + 1)..], out port) || port is < 1 or > 65535))
        {
            throw new JarvisException("Port HP tidak valid (bawaan 8010).");
        }
        if (!IPAddress.TryParse(host, out var ip) || ip.AddressFamily != AddressFamily.InterNetwork)
        {
            throw new JarvisException("Isi alamat IP HP, mis. 192.168.1.7:8010 (tertulis di tab JARVIS di HP).");
        }
        var b = ip.GetAddressBytes();
        var allowed = IPAddress.IsLoopback(ip) || IsPrivate(ip) || (b[0] == 100 && b[1] is >= 64 and <= 127) || (b[0] == 169 && b[1] == 254);
        if (!allowed)
        {
            throw new JarvisException("Hanya alamat jaringan lokal (mis. 192.168.x.x) atau VPN pribadi yang bisa dipakai.");
        }
        return $"{ip}:{port}";
    }

    /// <summary>A JSON body with a Content-Length (the phone's small server reads no chunked streams).</summary>
    private static StringContent JsonBody(object value) => new(JsonSerializer.Serialize(value), Encoding.UTF8, "application/json");

    private static HttpClient PhoneClient(TimeSpan timeout) =>
        new(new SocketsHttpHandler { UseProxy = false, AllowAutoRedirect = false, ConnectTimeout = TimeSpan.FromSeconds(4) }) { Timeout = timeout };

    private static async Task<JsonElement?> ReadJsonAsync(HttpResponseMessage response, CancellationToken ct)
    {
        try
        {
            var text = await ReadPrefixAsync(response, 65536, ct);
            return JsonDocument.Parse(text).RootElement.Clone();
        }
        catch (JsonException)
        {
            return null;
        }
    }

    private static string? Text(JsonElement? json, string name) =>
        json is { ValueKind: JsonValueKind.Object } j && j.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;

    public async Task<JarvisResult> PairPhoneAsync(string? address, string? key, CancellationToken ct)
    {
        string target;
        try
        {
            target = ParsePhoneAddress(address);
        }
        catch (JarvisException ex)
        {
            return Fail("invalid_address", ex.Message);
        }
        var code = (key ?? "").Trim().ToUpperInvariant().Replace("-", "").Replace(" ", "");
        if (code.Length != 8 || code.Any(c => !"ABCDEFGHJKMNPQRSTUVWXYZ23456789".Contains(c)))
        {
            return Fail("invalid_key", "Kode dari HP terdiri dari 8 huruf/angka (tanpa O, I, L, 0, 1).");
        }
        using var http = PhoneClient(TimeSpan.FromSeconds(10));
        try
        {
            using var response = await http.PostAsync($"http://{target}/pair", JsonBody(new { key = code }), ct);
            var json = await ReadJsonAsync(response, ct);
            var token = Text(json, "token");
            if (!response.IsSuccessStatusCode || string.IsNullOrEmpty(token))
            {
                return Fail("refused", Text(json, "message") ?? $"HP menolak pairing (HTTP {(int)response.StatusCode}).");
            }
            Directory.CreateDirectory(Path.GetDirectoryName(options.PhoneConfig)!);
            File.WriteAllText(options.PhoneConfig, JsonSerializer.Serialize(new PhoneConfigFile(target, token, DateTimeOffset.Now), PhoneJson));
            EnsurePlugin();
            Line($"HP {target} terpasang.");
            return new JarvisResult(true, null, _process is null
                ? "HP terpasang. JARVIS di PC bisa menyuruh HP setelah dijalankan."
                : "HP terpasang. Jalankan ulang JARVIS di PC agar perintah suara ke HP aktif.");
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or IOException or UnauthorizedAccessException)
        {
            return Fail("offline", $"HP {target} tidak menjawab ({ex.GetType().Name}). SHARX harus terbuka di HP dengan \"Izinkan PC mengontrol HP\" aktif, satu Wi-Fi.");
        }
    }

    public async Task<JarvisResult> PhoneCommandAsync(string? text, CancellationToken ct)
    {
        var command = string.Join(' ', (text ?? "").Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries));
        if (command.Length == 0)
        {
            return Fail("empty", "Perintah untuk HP kosong.");
        }
        if (command.Length > 2000)
        {
            command = command[..2000];
        }
        var phone = ReadPhone();
        if (phone is null)
        {
            return Fail("not_paired", "HP belum dipasangkan.");
        }
        using var http = PhoneClient(TimeSpan.FromSeconds(60));
        using var request = new HttpRequestMessage(HttpMethod.Post, $"http://{phone.Address}/command") { Content = JsonBody(new { text = command }) };
        request.Headers.Authorization = new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", phone.Token);
        try
        {
            using var response = await http.SendAsync(request, ct);
            var json = await ReadJsonAsync(response, ct);
            if (response.StatusCode == HttpStatusCode.Unauthorized)
            {
                return Fail("pairing_lost", "HP tidak lagi menerima PC ini. Buat kode baru di HP dan pasangkan lagi.");
            }
            var reply = Text(json, "reply");
            return reply is { Length: > 0 }
                ? new JarvisResult(true, null, reply)
                : Fail("no_reply", Text(json, "detail") ?? Text(json, "message") ?? $"HP menjawab HTTP {(int)response.StatusCode}.");
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or IOException)
        {
            return Fail("offline", $"HP {phone.Address} tidak menjawab ({ex.GetType().Name}).");
        }
    }

    public JarvisResult UnpairPhone()
    {
        try
        {
            File.Delete(options.PhoneConfig);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            return Fail("io", ex.Message);
        }
        return new JarvisResult(true, null, "HP dilupakan.");
    }

    // ------------------------------------------------------------------ manifest and log

    private JarvisInstall? ReadManifest()
    {
        var file = options.ManifestFile;
        DateTime stamp;
        try
        {
            stamp = File.Exists(file) ? File.GetLastWriteTimeUtc(file) : DateTime.MinValue;
        }
        catch (IOException)
        {
            return null;
        }
        lock (_gate)
        {
            if (_manifest.Stamp == stamp && stamp != DateTime.MinValue)
            {
                return _manifest.Value;
            }
        }
        JarvisInstall? value = null;
        if (stamp != DateTime.MinValue)
        {
            try
            {
                value = JsonSerializer.Deserialize<JarvisInstall>(File.ReadAllText(file));
            }
            catch (Exception ex) when (ex is IOException or JsonException or UnauthorizedAccessException)
            {
                log.LogWarning("JARVIS installed.json unreadable: {Message}", ex.Message);
            }
        }
        lock (_gate)
        {
            _manifest = (stamp, value);
        }
        return value;
    }

    private void WriteManifest(JarvisInstall install)
    {
        var temp = options.ManifestFile + ".tmp";
        File.WriteAllText(temp, JsonSerializer.Serialize(install, new JsonSerializerOptions { WriteIndented = true }));
        File.Move(temp, options.ManifestFile, overwrite: true);
    }

    [GeneratedRegex(@"\x1B\[[0-9;?]*[ -/]*[@-~]")]
    private static partial Regex AnsiEscape();

    private void Line(string? text)
    {
        if (text is null)
        {
            return;
        }
        // progress bars redraw with carriage returns; only the last state of a line is kept
        var line = text.TrimEnd('\r');
        var cr = line.LastIndexOf('\r');
        var clean = AnsiEscape().Replace(cr >= 0 ? line[(cr + 1)..] : line, "").TrimEnd();
        if (clean.Length == 0)
        {
            return;
        }
        if (clean.Length > 500)
        {
            clean = clean[..500] + " …";
        }
        lock (_gate)
        {
            _lines.AddLast((++_lineSeq, clean));
            while (_lines.Count > KeptLines)
            {
                _lines.RemoveFirst();
            }
            _logWriter?.WriteLine($"{DateTimeOffset.Now:yyyy-MM-dd HH:mm:ss} {clean}");
        }
    }

    private static JarvisResult Fail(string error, string message) => new(false, error, message);

    // ------------------------------------------------------------------ lifetime

    Task IHostedService.StartAsync(CancellationToken cancellationToken)
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(options.LogFile)!);
            var info = new FileInfo(options.LogFile);
            if (info.Exists && info.Length > 5_000_000)
            {
                File.Move(options.LogFile, options.LogFile + ".old", overwrite: true);
            }
            var writer = new StreamWriter(new FileStream(options.LogFile, FileMode.Append, FileAccess.Write, FileShare.ReadWrite), Encoding.UTF8)
            {
                AutoFlush = true,
            };
            lock (_gate)
            {
                _logWriter = writer;
            }
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            log.LogWarning("JARVIS log unavailable: {Message}", ex.Message);
        }
        return Task.CompletedTask;
    }

    async Task IHostedService.StopAsync(CancellationToken cancellationToken)
    {
        _stopping.Cancel();
        Task job;
        lock (_gate)
        {
            job = _jobTask;
        }
        // the desktop app gives the whole gateway 10 s to stop; whatever is left still ends with the kill-on-close job
        await Task.WhenAny(job, Task.Delay(TimeSpan.FromSeconds(3), CancellationToken.None));
        await StopJarvisAsync(TimeSpan.FromSeconds(4));
        lock (_gate)
        {
            _logWriter?.Dispose();
            _logWriter = null;
        }
    }

    public void Dispose()
    {
        _probe.Dispose();
        _jobCts?.Dispose();
        _stopping.Dispose();
        _probeLock.Dispose();
        lock (_gate)
        {
            _logWriter?.Dispose();
            _logWriter = null;
        }
    }
}
