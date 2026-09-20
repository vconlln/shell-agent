@echo off
rem ============================================================================
rem  One-click build (Windows): venv - deps - PyInstaller - artifact self-test
rem
rem  Output: dist\windows\tu-shell-agent\tu-shell-agent.exe
rem  Usage : double-click this file, or run packaging\windows\build.bat in cmd
rem
rem  THIS FILE IS INTENTIONALLY PURE ASCII -- do not add non-ASCII text.
rem  cmd.exe reads a .bat with the active code page. A UTF-8 .bat that contains
rem  multi-byte text gets split mid-character while being read, and fragments of
rem  comments are then executed as commands, e.g.
rem      '...' is not recognized as an internal or external command
rem  (observed on a Chinese Windows with code page 936). All Chinese
rem  explanations live in packaging/build.md instead.
rem
rem  NOT RUN ON WINDOWS YET: PyInstaller cannot cross-compile, so this script can
rem  only be verified on a Windows machine. It mirrors packaging/linux/build.sh
rem  step by step on purpose (same spec, same steps, same self-test, same plugin
rem  check), so the two platforms cannot drift apart.
rem ============================================================================

setlocal
pushd "%~dp0..\.." || goto :fail
echo Repo root: %CD%
echo.

rem ---- Step 1/5: virtual environment -----------------------------------------
if exist ".venv\Scripts\python.exe" goto :have_venv
echo [1/5] Creating virtual environment .venv ...
py -3 -m venv .venv
if exist ".venv\Scripts\python.exe" goto :deps
echo "py" launcher not available, retrying with "python" ...
python -m venv .venv || goto :fail
goto :deps

:have_venv
echo [1/5] .venv already exists, skipping creation

rem ---- Step 2/5: dependencies ------------------------------------------------
:deps
echo [2/5] Preparing dependencies ...
rem The three steps below have different consequences: upgrading pip only warns;
rem PySide6 and pyinstaller are required to produce an artifact; the editable
rem install only affects running the tests and the console entry point, so a
rem failure there (usually a flaky network) must not fail the whole build.
".venv\Scripts\python.exe" -m pip install --upgrade pip
echo   - PySide6 and pyinstaller (required)
".venv\Scripts\python.exe" -m pip install "PySide6>=6.11" pyinstaller || goto :fail
echo   - the project itself and test deps (optional)
".venv\Scripts\python.exe" -m pip install -e ".[ui,dev]"
if not errorlevel 1 goto :deps_ok
echo   ! editable install failed (network or proxy); continuing with packaging
:deps_ok

rem ---- Step 3/5: package -----------------------------------------------------
echo [3/5] Packaging (one-folder) ...
rem --distpath / --workpath are resolved relative to the current directory, so the
rem pushd above is required. The two platforms use separate output directories:
rem dist\windows and dist/linux never overwrite each other.
".venv\Scripts\python.exe" -m PyInstaller --clean --noconfirm --distpath "dist\windows" --workpath "build\windows" "packaging\tu-shell-agent.spec" || goto :fail

rem ---- Step 4/5: artifact self-test ------------------------------------------
set "APPDIR=dist\windows\tu-shell-agent"
set "APP=%APPDIR%\tu-shell-agent.exe"
if not exist "%APP%" goto :missing
echo [4/5] Self-test of the artifact (expects exit=0) ...
rem The spec sets console=False, so the artifact is a GUI subsystem exe: calling it
rem directly neither waits nor exposes an exit code, hence "start /wait".
rem QT_QPA_PLATFORM is not set here: opening a real window is closer to actual use
rem and depends on one Qt plugin less.
start /wait "" "%APP%" --self-test
echo self-test exit=%ERRORLEVEL%
if not "%ERRORLEVEL%"=="0" goto :selftest_failed

rem ---- Step 5/5: Qt plugin spot-check ----------------------------------------
rem PyInstaller does not fail when a Qt plugin is missing. The window then either
rem refuses to start (platform plugin) or the self-drawn acrylic background
rem silently degrades to plain translucency (jpeg decoding) -- both only show up
rem when a human runs the app. The search below is recursive on purpose: it must
rem not depend on where the PySide6 wheel keeps its plugins.
echo [5/5] Checking that the Qt plugins were collected ...
set "HAVE_PLATFORM="
for /f "delims=" %%F in ('dir /s /b "%APPDIR%\qwindows.dll" 2^>nul') do set "HAVE_PLATFORM=%%F"
if not defined HAVE_PLATFORM goto :missing_platform
set "HAVE_JPEG="
for /f "delims=" %%F in ('dir /s /b "%APPDIR%\qjpeg.dll" 2^>nul') do set "HAVE_JPEG=%%F"
if not defined HAVE_JPEG goto :missing_jpeg
echo   Qt platform plugin : %HAVE_PLATFORM%
echo   Qt image plugin    : %HAVE_JPEG%

echo.
echo ============================================================
echo  Artifact: %CD%\%APP%
for %%F in ("%APP%") do echo  exe size: %%~zF bytes
echo  Double-click it to open the interface
echo  Generating scripts requires a logged-in opencode: opencode auth login
echo  Manual test checklist: packaging\build.md section 6
echo ============================================================
popd
pause
exit /b 0

:missing
echo *** FAILED: packaging finished but %APP% does not exist ***
goto :fail

:selftest_failed
echo *** FAILED: the self-test did not return 0; the artifact builds but does not run.
echo     See packaging\build.md section 3 for troubleshooting.
goto :fail

:missing_platform
echo *** FAILED: qwindows.dll (Qt platform plugin) is missing from the artifact.
echo     The exe will not start without it.
echo     See packaging\build.md section 4 (which warnings are noise, which are real).
goto :fail

:missing_jpeg
echo *** FAILED: qjpeg.dll (Qt image format plugin) is missing from the artifact.
echo     The self-drawn acrylic background needs it to read the wallpaper; without
echo     it the app silently falls back to plain translucency.
echo     See packaging\build.md section 4 (which warnings are noise, which are real).
goto :fail

:fail
echo.
echo *** FAILED: see the output above; packaging\build.md section 1 has the
echo     prerequisites and section 8 covers troubleshooting. ***
popd
pause
exit /b 1
