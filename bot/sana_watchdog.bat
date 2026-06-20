@echo off
REM Sana watchdog — держит бота живым: перезапуск при падении + защита от дублей.
cd /d "C:\Users\Acer\AI_Assistant\projects\jarvis\bot"
set PY="C:\Users\Acer\AppData\Local\Python\pythoncore-3.14-64\python.exe"

set PYW="C:\Users\Acer\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe"

:loop
REM убить прежние экземпляры бота И веб-сервера (без дублей)
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { ($_.CommandLine -like '*jarvis_bot.py*' -or $_.CommandLine -like '*sana_web.py*') -and $_.ProcessId -ne $PID } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" 2>nul
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*sana_web.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" 2>nul

REM веб-центр в фоне (скрыто), потом бот в foreground (держит цикл)
start "" %PYW% sana_web.py
%PY% jarvis_bot.py >> bot.log 2>> bot.err.log

REM упал — пауза и перезапуск
timeout /t 5 /nobreak >nul
goto loop
