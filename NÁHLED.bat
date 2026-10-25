@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
title NAHLED editu
rem Hrubý náhled editu z tvých souborů – přehrávání na PC, na mobilu doma (Wi-Fi) a přes Tailscale kdekoli.
rem Nech okno otevřené, dokud se díváš. Odkazy jsou i v záložce Výroba na webu Brawl Trendy.
py "%~dp0tools\nahled_server.py"
pause
