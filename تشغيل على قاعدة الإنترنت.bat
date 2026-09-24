@echo off
cd /d "%~dp0"
title Jadwal Madrasa - cloud database (Turso)
rem Uses .env : connects to the online database. Slower, for checking only.
python app.py
pause
