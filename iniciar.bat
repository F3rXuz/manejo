@echo off
title Asistente de Voz y Texto para Windows
cd /d "%~dp0"
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" main.py
if errorlevel 1 pause

