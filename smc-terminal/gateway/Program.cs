using SmcTerminal.Gateway;

// Browser mode: `dotnet run -- --Terminal:ManageEngine=true` serves the UI on the configured URL.
// The desktop app (desktop/) hosts the same gateway in-process via GatewayHost.Build.
GatewayHost.Build(args).Run();
