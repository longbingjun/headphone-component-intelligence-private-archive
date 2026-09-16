@echo off
setlocal
for %%I in ("%~dp0..") do set "APP_ROOT=%%~fI"
cd /d "%APP_ROOT%"

where uv >nul 2>nul
if errorlevel 1 (
  echo [ERROR] uv is required. Install uv or create .venv with Python 3.12 manually.
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating Python 3.12 environment on D drive...
  uv venv --python 3.12 .venv
  if errorlevel 1 exit /b 1
)

echo Installing Python dependencies...
uv pip install --python ".venv\Scripts\python.exe" -r requirements.txt
if errorlevel 1 exit /b 1

echo Installing Web dependencies...
pushd web
call npm ci
set "NPM_RESULT=%ERRORLEVEL%"
popd
if not "%NPM_RESULT%"=="0" exit /b %NPM_RESULT%

echo.
echo Local development dependencies are ready.
echo Start with: scripts\run-local-dev.cmd
endlocal
