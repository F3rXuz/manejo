@echo off
title Jarvis - Asistente para Windows
cd /d "%~dp0"

REM Si existe un archivo .env local, carga las variables
if exist .env (
    for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
        if not "%%a"=="" set "%%a=%%b"
    )
)

python main.py
if errorlevel 1 pause

