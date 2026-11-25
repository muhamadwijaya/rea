using Sharx.App;
using Sharx.Web;

namespace Sharx.Desktop;

internal static class Program
{
    [STAThread]
    private static void Main(string[] args)
    {
        using var single = new Mutex(true, @"Local\SharxDataTransfer.Desktop", out var first);
        if (!first)
        {
            MessageBox.Show("SHARX Data Transfer sudah berjalan.", "SHARX Data Transfer",
                MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }

        ApplicationConfiguration.Initialize();

        // Error tak terduga di UI dicatat ke data\crash.log dan aplikasi tetap berjalan.
        Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException);

        var options = new SharxHostOptions { Port = 0 };
        for (var i = 0; i < args.Length; i++)
        {
            if (args[i] == "--data" && i + 1 < args.Length)
            {
                options.DataDirectory = Path.GetFullPath(args[++i]);
            }
            else if (args[i] == "--port" && i + 1 < args.Length && int.TryParse(args[++i], out var port))
            {
                options.Port = port;
            }
        }

        SharxRuntime.InstallCrashGuards(options.DataDirectory);
        Application.ThreadException += (_, e) =>
            SharxRuntime.WriteCrash(options.DataDirectory, "ThreadException", e.Exception);

        Microsoft.AspNetCore.Builder.WebApplication app;
        try
        {
            app = SharxWebHost.Build(options, args);
        }
        catch (AlreadyRunningException ex)
        {
            MessageBox.Show(ex.Message, "SHARX Data Transfer", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }

        Application.Run(new MainForm(app));
    }
}
