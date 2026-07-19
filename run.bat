@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel%==0 (
    python main.py
    set RUN_EXIT=%errorlevel%
) else (
    where py >nul 2>nul
    if %errorlevel%==0 (
        py main.py
        set RUN_EXIT=%errorlevel%
    ) else (
        echo Python not found on PATH. Install Python from python.org and try again.
        pause
        endlocal
        exit /b 1
    )
)

rem Only pause on a crash/error exit so you can read what happened; a normal
rem close of the app window just closes this console too, no keypress needed.
if not "%RUN_EXIT%"=="0" (
    echo.
    echo main.py exited with an error ^(code %RUN_EXIT%^).
    pause
)

endlocal
