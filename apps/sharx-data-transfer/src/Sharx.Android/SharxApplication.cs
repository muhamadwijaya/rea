using Android.App;
using Android.Runtime;

namespace Sharx.Android;

/// <summary>Kode pertama yang berjalan di proses: pasang pencatat error sebelum layar / service dibuat.</summary>
[Application]
public sealed class SharxApplication : Application
{
    public SharxApplication(IntPtr handle, JniHandleOwnership ownership)
        : base(handle, ownership)
    {
    }

    public override void OnCreate()
    {
        base.OnCreate();
        CrashLog.Init(FilesDir?.AbsolutePath ?? System.Environment.GetFolderPath(System.Environment.SpecialFolder.Personal));
        AndroidEnvironment.UnhandledExceptionRaiser += (_, e) => CrashLog.Write("Unhandled (.NET)", e.Exception, fatal: true);
        AppDomain.CurrentDomain.UnhandledException += (_, e) => CrashLog.Write("Unhandled (AppDomain)", e.ExceptionObject, fatal: true);
        TaskScheduler.UnobservedTaskException += (_, e) =>
        {
            CrashLog.Write("Unobserved task", e.Exception);
            e.SetObserved();
        };

        // Error Java di thread mana pun: catat, lalu teruskan ke handler bawaan.
        var previous = Java.Lang.Thread.DefaultUncaughtExceptionHandler;
        Java.Lang.Thread.DefaultUncaughtExceptionHandler = new JavaCrashHandler(previous);
    }

    private sealed class JavaCrashHandler(Java.Lang.Thread.IUncaughtExceptionHandler? previous)
        : Java.Lang.Object, Java.Lang.Thread.IUncaughtExceptionHandler
    {
        public void UncaughtException(Java.Lang.Thread t, Java.Lang.Throwable e)
        {
            var trace = new Java.IO.StringWriter();
            e.PrintStackTrace(new Java.IO.PrintWriter(trace));
            CrashLog.Write($"Unhandled (Java, thread {t.Name})", trace.ToString(), fatal: true);
            previous?.UncaughtException(t, e);
        }
    }
}
