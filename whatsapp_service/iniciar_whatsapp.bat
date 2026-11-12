@echo off
title Jarvis - Servicio de WhatsApp
cd /d "%~dp0"

REM Agregar Node.js al PATH en caso de que la terminal no se haya reiniciado
set "PATH=C:\Program Files\nodejs;%PATH%"

REM Liberar el puerto 3000 si quedo alguna instancia previa abierta
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":3000" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)

echo ============================================================
echo         INICIANDO SERVICIO DE WHATSAPP PARA JARVIS
echo ============================================================
node server.js
if errorlevel 1 pause

