# Build paket Windows siap pakai (butuh .NET 8 SDK).
#   powershell -ExecutionPolicy Bypass -File publish.ps1
# Hasil:
#   publish\desktop\SharxDataTransfer.exe        aplikasi desktop (WebView2, jendela seperti desain)
#   publish\server\SharxDataTransfer.Server.exe  mode browser / service (http://127.0.0.1:5080)
#   publish\simulator\SharxSimulator.exe         simulator server & printer TIJ untuk uji coba
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$common = @("-c", "Release", "-r", "win-x64", "--self-contained", "true", "-p:PublishSingleFile=true")

dotnet test tests/Sharx.Core.Tests/Sharx.Core.Tests.csproj -c Release
dotnet publish src/Sharx.Desktop/Sharx.Desktop.csproj @common -o publish/desktop
dotnet publish src/Sharx.Host/Sharx.Host.csproj @common -o publish/server
dotnet publish tools/Sharx.Simulator/Sharx.Simulator.csproj @common -o publish/simulator

Write-Host "Selesai. Jalankan publish\desktop\SharxDataTransfer.exe"
