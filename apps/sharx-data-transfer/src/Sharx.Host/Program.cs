using System.Diagnostics;
using System.Globalization;
using Microsoft.Extensions.Hosting;
using Sharx.Web;

// Mode browser / server: menjalankan UI di http://127.0.0.1:5080 dan (opsional) membuka browser.
//   SharxDataTransfer.Server [--port 5080] [--data <folder>] [--no-browser] [--allow-remote]
var options = new SharxHostOptions();
var openBrowser = true;
for (var i = 0; i < args.Length; i++)
{
    switch (args[i])
    {
        case "--port" when i + 1 < args.Length:
            options.Port = int.Parse(args[++i], CultureInfo.InvariantCulture);
            break;
        case "--data" when i + 1 < args.Length:
            options.DataDirectory = Path.GetFullPath(args[++i]);
            break;
        case "--no-browser":
            openBrowser = false;
            break;
        case "--allow-remote":
            options.AllowRemote = true;
            break;
        case "-h" or "--help":
            Console.WriteLine("SharxDataTransfer.Server [--port 5080] [--data <folder>] [--no-browser] [--allow-remote]");
            return 0;
    }
}

Microsoft.AspNetCore.Builder.WebApplication app;
try
{
    app = SharxWebHost.Build(options);
}
catch (AlreadyRunningException ex)
{
    Console.Error.WriteLine(ex.Message);
    return 2;
}

await app.StartAsync();
var url = SharxWebHost.ListeningAddress(app);
Console.WriteLine($"SHARX Data Transfer v{SharxWebHost.Version}");
Console.WriteLine($"UI   : {url}");
Console.WriteLine($"Data : {options.DataDirectory}");
Console.WriteLine("Tekan Ctrl+C untuk berhenti.");

if (openBrowser)
{
    try
    {
        Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
    }
    catch (Exception ex) when (ex is System.ComponentModel.Win32Exception or InvalidOperationException or PlatformNotSupportedException)
    {
        Console.WriteLine("Buka alamat UI di browser secara manual.");
    }
}

await app.WaitForShutdownAsync();
return 0;
