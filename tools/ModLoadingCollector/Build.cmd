@echo off
setlocal
cd /d "%~dp0"
where dotnet >nul 2>nul
if errorlevel 1 (
  echo Building from source requires a .NET SDK. The prebuilt XXMI.Capture.exe needs only Windows' .NET Framework 4.8.
  exit /b 1
)
dotnet build ModLoadingCollector.csproj --configuration Release
if errorlevel 1 exit /b 1
echo.
echo Built: %~dp0bin\Release\net48\XXMI.Capture.exe
endlocal
