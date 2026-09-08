@echo off
rem Ripple launcher for Windows. Double-click in Explorer, or run from a
rem terminal.
rem
rem   launch.bat                 start the app and open a browser
rem   launch.bat --test [args]   run the test suite instead, passing any
rem                              further arguments to pytest
rem
rem The Windows counterpart of launch.command: stops any running instance,
rem builds or repairs the venv, installs the package, picks a free port,
rem starts the server, and opens a browser once it answers.

setlocal enabledelayedexpansion
cd /d "%~dp0"

set "VENV=%CD%\.venv"
set "PYTHON=%VENV%\Scripts\python.exe"
if "%RIPPLE_PORT%"=="" (set "BASE_PORT=8420") else (set "BASE_PORT=%RIPPLE_PORT%")

rem Two ways to run: a desktop and a host. A host (Replit, Render, Fly)
rem names the one port it proxies in PORT and reaches the app across the
rem container boundary, so that run takes the port as given, binds every
rem interface, and wants neither a browser nor the reloader. A desktop sets
rem neither variable and keeps the behaviour it always had. RIPPLE_HOST
rem overrides the bind address on its own.
set "SERVED_PORT=%PORT%"
if "%SERVED_PORT%"=="" (
  if "%RIPPLE_HOST%"=="" (set "HOST=127.0.0.1") else (set "HOST=%RIPPLE_HOST%")
) else (
  if "%RIPPLE_HOST%"=="" (set "HOST=0.0.0.0") else (set "HOST=%RIPPLE_HOST%")
)
set "HOST_IS_LOOPBACK="
if "%HOST%"=="127.0.0.1" set "HOST_IS_LOOPBACK=yes"
if /i "%HOST%"=="localhost" set "HOST_IS_LOOPBACK=yes"

echo Ripple  -  %CD%
echo.

rem Stop any instance already running, before anything else. A leftover
rem server from an earlier session keeps serving stale code indefinitely.
rem The match is on the process command line, not on a port, so an instance
rem started by hand on another port is found too.
powershell -NoProfile -Command ^
  "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'uvicorn ripple\.web\.app' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>nul

rem The venv, when it exists and meets the 3.11 floor, is the only
rem interpreter this script needs; the search below runs only to create or
rem rebuild it. The py launcher is asked for specific versions first, since
rem a bare python on PATH can be the Store stub or an old pin.
"%PYTHON%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if not errorlevel 1 goto deps

set "SYSTEM_PYTHON="
for %%C in ("py -3.13" "py -3.12" "py -3.11" "py -3" "python") do (
  if not defined SYSTEM_PYTHON (
    call %%~C -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
    if !errorlevel! == 0 set "SYSTEM_PYTHON=%%~C"
  )
)
if not defined SYSTEM_PYTHON (
  echo No Python 3.11 or newer was found on this machine. 1>&2
  echo Install one from https://www.python.org/downloads/ and run this 1>&2
  echo script again. 1>&2
  exit /b 1
)
echo Creating the virtual environment with %SYSTEM_PYTHON%...
if exist "%VENV%" rmdir /s /q "%VENV%"
call %SYSTEM_PYTHON% -m venv "%VENV%"

:deps
echo Installing dependencies...
"%PYTHON%" -m pip install --quiet --upgrade pip
"%PYTHON%" -m pip install --quiet -e ".[dev]"
if errorlevel 1 (
  echo The install failed; the messages above say why. 1>&2
  exit /b 1
)

if "%~1"=="--test" (
  echo Running the test suite...
  echo.
  "%PYTHON%" -m pytest %2 %3 %4 %5 %6 %7 %8 %9
  exit /b !errorlevel!
)

rem A host proxies exactly one port, so scanning past it would publish a
rem server nothing routes to. Take it as given, and let the bind fail loudly
rem if something already holds it.
set "PORT="
if defined SERVED_PORT (
  set "PORT=%SERVED_PORT%"
) else (
  rem Find a free port, starting at the default and trying up to twenty
  rem above it. The kill above already cleared any same-app holder, so a
  rem busy port here belongs to something else and is skipped rather than
  rem taken.
  for /l %%O in (0,1,20) do (
    if not defined PORT (
      set /a candidate=%BASE_PORT%+%%O
      netstat -ano | findstr /r /c:":!candidate! .*LISTENING" >nul 2>nul
      if !errorlevel! == 1 set "PORT=!candidate!"
    )
  )
  if not defined PORT (
    echo No free port between %BASE_PORT% and 20 above it. 1>&2
    echo Close whatever holds those ports, or set RIPPLE_PORT to another 1>&2
    echo starting port, then run this script again. 1>&2
    exit /b 1
  )
)

echo.
echo Optional tools:
for %%B in (tesseract pdftoppm) do (
  where %%B >nul 2>nul
  if !errorlevel! == 0 (
    echo   %%B: present
  ) else (
    echo   %%B: absent ^(scanned PDFs will be rejected rather than OCR'd^)
  )
)

set "URL=http://127.0.0.1:%PORT%"
echo.
if defined HOST_IS_LOOPBACK (
  echo Starting Ripple on %URL%
) else (
  echo Starting Ripple on %HOST%:%PORT%
)
echo Press Control-C to stop.
echo.

rem Open the browser once the server answers, rather than immediately, so
rem the first page load is not a connection error. A run bound past loopback
rem is being served to someone else's browser, so there is none to open here.
if defined HOST_IS_LOOPBACK (
  start "" /b powershell -NoProfile -Command ^
    "for ($i = 0; $i -lt 40; $i++) { try { Invoke-WebRequest -UseBasicParsing '%URL%' | Out-Null; Start-Process '%URL%'; break } catch { Start-Sleep -Milliseconds 250 } }"
)

rem --reload restarts the server when a Python file changes. Templates and
rem static assets need no restart: templates re-read on render, and every
rem response is sent no-store. A host runs code that no one is editing, and
rem the reloader's file watching costs it memory for nothing.
if defined SERVED_PORT (
  "%PYTHON%" -m uvicorn ripple.web.app:app --host %HOST% --port %PORT% --log-level info
  exit /b !errorlevel!
)
"%PYTHON%" -m uvicorn ripple.web.app:app --host %HOST% --port %PORT% --log-level info --reload --reload-dir "%CD%\ripple"
