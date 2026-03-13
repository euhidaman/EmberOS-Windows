@echo off
REM EmberOS-Windows CLI entry point
REM Usage: emberos.bat [command]
REM   (no args)  — Start interactive REPL
REM   gui        — Launch the GUI window
REM   repl       — Start interactive REPL
REM   chat | status | start | stop | query "text" | logs | install | uninstall

setlocal
set "ROOT=%~dp0"
set "PYTHON=%ROOT%env\python-embed\python.exe"

if not exist "%PYTHON%" (
    set "PYTHON=%ROOT%env\venv\Scripts\python.exe"
)

if not exist "%PYTHON%" (
    echo ERROR: Python runtime not found. Run setup.ps1 first.
    exit /b 1
)

if "%~1"=="gui" (
    start "" "%PYTHON%" -m emberos.gui
    exit /b 0
)

"%PYTHON%" -m emberos.cli %*
