@echo off
rem Zastavi neviditelne sledovani casu v DaVinci. Znovu ho pustis SLEDOVANI CASU.bat nebo restartem PC.
"S:\EDIT\venv\Scripts\python.exe" "%~dp0tools\vyroba_sledovani.py" --stop
timeout /t 3 >nul
