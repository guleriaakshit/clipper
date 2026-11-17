@echo off
rem Run Clipper from anywhere on Windows: clipper serve ^| analyze ^<url^> ^| live ^<url^> ^| render ^<job^> ^<n^> ^| update ^| ...
rem install.ps1 puts a small clipper.cmd on your PATH that calls this one.
setlocal
set "CLIPPER_HOME=%~dp0"
set "CLIPPER_HOME=%CLIPPER_HOME:~0,-1%"
if not exist "%CLIPPER_HOME%\.venv\Scripts\python.exe" echo Clipper isn't set up yet. Run install.ps1 in "%CLIPPER_HOME%". & exit /b 1
rem yt-dlp lives in the venv; Python reads and writes UTF-8; Gradio checks localhost, never via a proxy
set "PATH=%CLIPPER_HOME%\.venv\Scripts;%PATH%"
set "PYTHONUTF8=1"
set "NO_PROXY=localhost,127.0.0.1,%NO_PROXY%"
set "no_proxy=localhost,127.0.0.1,%no_proxy%"
"%CLIPPER_HOME%\.venv\Scripts\python.exe" -m clipper %*
exit /b %ERRORLEVEL%
