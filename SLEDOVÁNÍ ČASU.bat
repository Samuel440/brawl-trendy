@echo off
rem Sledovani casu v DaVinci - bezi neviditelne na pozadi, spousti se samo po zapnuti PC.
rem Ukazuje se nahore v zalozce Vyroba na webu Brawl Trendy. Druhe spusteni nic neudela.
rem Zastaveni: ZASTAVIT SLEDOVANI.bat
start "" "S:\EDIT\venv\Scripts\pythonw.exe" "%~dp0tools\vyroba_sledovani.py"
