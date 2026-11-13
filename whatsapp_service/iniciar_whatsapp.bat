@echo off
title Jarvis - Servicio de WhatsApp
cd /d "%~dp0"

REM Agregar Node.js al PATH en caso de que la terminal no se haya reiniciado
set "PATH=C:\Program Files\nodejs;%PATH%"

echo ============================================================
echo         INICIANDO SERVICIO DE WHATSAPP PARA JARVIS
echo ============================================================
node server.js
if errorlevel 1 pause

