@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
title SLEDOVANI CASU v DaVinci
rem Počítá minuty, kdy v DaVinci opravdu pracuješ (okno v popředí + myš/klávesnice), po projektech.
rem Živě se to ukazuje nahoře v záložce Výroba na webu Brawl Trendy (i na mobilu). Okno můžeš minimalizovat.
"S:\EDIT\venv\Scripts\python.exe" "%~dp0tools\vyroba_sledovani.py"
pause
