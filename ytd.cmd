@echo off
rem ytd launcher for Windows (portable edition).
rem The first run sets up everything inside this folder (runtime\), later runs start instantly.
rem   ytd.cmd "https://www.youtube.com/watch?v=XXXXXXXXXXX"            video, 1080p
rem   ytd.cmd "https://www.youtube.com/watch?v=XXXXXXXXXXX" --type audio
rem   ytd.cmd --setup          repair / re-run the setup
rem   ytd.cmd --add-to-path    make "ytd" work from any folder
setlocal
set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "ARCH=x64"
if /i "%PROCESSOR_ARCHITECTURE%"=="ARM64" set "ARCH=arm64"
if /i "%PROCESSOR_ARCHITEW6432%"=="ARM64" set "ARCH=arm64"
set "RUNTIME=%ROOT%\runtime\windows-%ARCH%"
set "PY=%RUNTIME%\venv\Scripts\python.exe"

if /i "%~1"=="--setup" goto force_setup
if /i "%~1"=="--repair" goto force_setup
if /i "%~1"=="--add-to-path" goto add_to_path

if not exist "%RUNTIME%\.ready" goto first_run
if not exist "%PY%" goto first_run
"%PY%" -c "import yt_dlp" >nul 2>&1 || goto first_run
goto run

:first_run
echo First run: setting up ytd in %RUNTIME% (this happens only once)...
call :setup || goto setup_failed
goto run

:force_setup
if exist "%RUNTIME%\.ready" del "%RUNTIME%\.ready"
call :setup
exit /b %ERRORLEVEL%

:setup
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\setup\bootstrap.ps1" -Root "%ROOT%" -Runtime "%RUNTIME%"
exit /b %ERRORLEVEL%

:setup_failed
echo Setup failed. Fix the problem shown above, then run: "%ROOT%\ytd.cmd" --setup
exit /b 1

:add_to_path
set "YTD_ROOT=%ROOT%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=$env:YTD_ROOT; $p=[Environment]::GetEnvironmentVariable('Path','User'); if(-not $p){$p=''}; if(($p.Split(';')) -notcontains $d){[Environment]::SetEnvironmentVariable('Path',(($p.TrimEnd(';')+';'+$d).TrimStart(';')),'User')}; Write-Host 'Done. Open a new terminal window and type: ytd --version'"
exit /b %ERRORLEVEL%

:run
set "PATH=%RUNTIME%\bin;%RUNTIME%\venv\Scripts;%PATH%"
"%PY%" "%ROOT%\youtube_downloader.py" %*
exit /b %ERRORLEVEL%
