@echo off
setlocal
cd /d "%~dp0"
rem Prefer the official Windows Python launcher, then PATH.
where py >nul 2>nul
if errorlevel 1 (
    set "PE_PY=python"
) else (
    set "PE_PY=py -3"
)
%PE_PY% -c "import sys; assert sys.version_info >= (3, 10)" >nul 2>nul
if errorlevel 1 (
    echo Python 3.10 or newer is required. Install from https://www.python.org/downloads/
    pause
    exit /b 1
)
if "%~1"=="hotkey" goto hotkey
if "%~1"=="proxy" goto proxy
if "%~1"=="mock" goto mock
if "%~1"=="test" goto test
if "%~1"=="build" goto build
if "%~1"=="release" goto release
if "%~1"=="app" goto app
if "%~1"=="" goto app
 echo Usage: start.bat [app^|hotkey^|proxy^|mock^|test^|build^|release]
exit /b 2

:app
%PE_PY% -c "import tkinter" >nul 2>nul
if errorlevel 1 (
    echo This Python has no tkinter. Install the official Python distribution with Tcl/Tk.
    pause
    exit /b 1
)
start "" %PE_PY% desktop\main.py
exit /b %errorlevel%

:hotkey
%PE_PY% hotkey.py
exit /b %errorlevel%

:proxy
%PE_PY% proxy.py
exit /b %errorlevel%

:mock
%PE_PY% tools\mock_llm.py 18080
exit /b %errorlevel%

:test
%PE_PY% -m unittest discover -s tests -v
exit /b %errorlevel%

:build
%PE_PY% desktop\build_exe.py
exit /b %errorlevel%

:release
%PE_PY% tools\make_release.py --build
exit /b %errorlevel%
