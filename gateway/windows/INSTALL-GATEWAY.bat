@echo off
chcp 65001 >nul
title SOS LoRa - Gateway setup
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Setup-Gateway.ps1" %*
echo.
pause
