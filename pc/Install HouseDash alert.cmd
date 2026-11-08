@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0newdevice-alert.ps1" -Install
timeout /t 3 >nul
