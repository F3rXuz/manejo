@echo off
title Jarvis - Servicio de WhatsApp
cd /d "%~dp0"
echo ============================================================
echo         INICIANDO SERVICIO DE WHATSAPP PARA JARVIS
echo ============================================================
node server.js
if errorlevel 1 pause
