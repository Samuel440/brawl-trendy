@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Brawl Trendy bezi na http://localhost:8765  (toto okno nech otevrene)
start "" http://localhost:8765
py server.py
