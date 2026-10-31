@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3.12 "%SCRIPT_DIR%image_compress.py" %*
    if %errorlevel%==0 exit /b 0
    py -3 "%SCRIPT_DIR%image_compress.py" %*
    exit /b %errorlevel%
)

python "%SCRIPT_DIR%image_compress.py" %*
exit /b %errorlevel%
