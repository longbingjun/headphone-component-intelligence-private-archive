@echo off
setlocal
for %%I in ("%~dp0..") do set "APP_ROOT=%%~fI"
set "LOCAL_ROOT=%~dp0.."
cd /d "%APP_ROOT%"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Python environment is missing.
  echo Run scripts\setup-local-dev.cmd first.
  exit /b 1
)
if not exist "web\node_modules" (
  echo [ERROR] Web dependencies are missing.
  echo Run scripts\setup-local-dev.cmd first.
  exit /b 1
)

if not exist "%LOCAL_ROOT%\runtime" mkdir "%LOCAL_ROOT%\runtime"
if not exist "%LOCAL_ROOT%\runtime\objects" mkdir "%LOCAL_ROOT%\runtime\objects"
if not exist "%LOCAL_ROOT%\runtime\video-temp" mkdir "%LOCAL_ROOT%\runtime\video-temp"

set "DATABASE_URL=sqlite+pysqlite:///%LOCAL_ROOT:\=/%/runtime/app.db"
set "STORAGE_BACKEND=local"
set "LOCAL_STORAGE_PATH=%LOCAL_ROOT%\runtime\objects"
set "SITE_DIR=%APP_ROOT%\site"
set "IMAGE_CACHE_DIR=%APP_ROOT%\web\public\images"
set "PUBLIC_BASE_PATH=/"
set "PUBLIC_IMAGE_BASE=/media"
set "AUTO_MIGRATE=false"
set "IMPORT_ON_START=false"
set "INITIAL_REFRESH_ON_START=false"
set "SCHEDULER_ENABLED=false"
set "REFRESH_STATIC_SITE=false"
set "REBUILD_SITE_ON_START=false"
set "INITIAL_IMAGE_SYNC_ON_START=false"
set "FULL_IMAGE_SYNC_ON_START=false"

echo Migrating local SQLite database...
call ".venv\Scripts\python.exe" -m server.cli migrate
if errorlevel 1 exit /b 1

echo Importing repository data and applying the video product gate...
call ".venv\Scripts\python.exe" -m server.cli import-data
if errorlevel 1 exit /b 1

if /I "%~1"=="--rebuild" goto build_site
if exist "site\index.html" goto start_server

:build_site
echo Preparing and building the local Web site...
call ".venv\Scripts\python.exe" scripts\prepare_web_data.py
if errorlevel 1 exit /b 1
pushd web
set "ASTRO_OUT_DIR=../site"
call npm run build:minio
set "BUILD_RESULT=%ERRORLEVEL%"
popd
if not "%BUILD_RESULT%"=="0" exit /b %BUILD_RESULT%

:start_server
echo Verifying curated, Web and built-site catalog versions...
call ".venv\Scripts\python.exe" scripts\release_acceptance.py --catalog-only
if errorlevel 1 (
  echo [ERROR] Local catalog is stale. Restart with scripts\run-local-dev.cmd --rebuild.
  exit /b 1
)
echo.
echo Local site: http://127.0.0.1:8000/
echo API health: http://127.0.0.1:8000/health
echo Press Ctrl+C to stop.
call ".venv\Scripts\python.exe" -m uvicorn server.main:app --host 127.0.0.1 --port 8000 --workers 1
endlocal
