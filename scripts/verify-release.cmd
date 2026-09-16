@echo off
setlocal
cd /d "%~dp0\.."
if not exist ".venv\Scripts\python.exe" (
  echo Local virtual environment is missing. Run scripts\setup-local-dev.cmd first.
  exit /b 2
)
if not defined DATABASE_URL set "DATABASE_URL=sqlite+pysqlite:///%CD:\=/%/runtime/acceptance.db"
if not defined STORAGE_BACKEND set "STORAGE_BACKEND=local"
if not defined LOCAL_STORAGE_PATH set "LOCAL_STORAGE_PATH=%CD%\runtime\acceptance-objects"
if not defined SITE_DIR set "SITE_DIR=%CD%\site"
if not defined PUBLIC_BASE_PATH set "PUBLIC_BASE_PATH=/"
if not defined PUBLIC_IMAGE_BASE set "PUBLIC_IMAGE_BASE=/media"
".venv\Scripts\python.exe" scripts\release_acceptance.py --mode local %*
exit /b %errorlevel%
