@echo off
cd /d "%~dp0"
title Jadwal Madrasa - local (SQLite)
rem Force the fast local database, ignoring .env
set TURSO_DATABASE_URL=
set TURSO_AUTH_TOKEN=
set APP_PASSWORD=
python app.py
pause
