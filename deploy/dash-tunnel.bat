@echo off
REM Sana Corp — дашборд команды. Поднимает SSH-туннель ноут->VM и открывает в браузере.
REM Окно держит туннель открытым; закрой окно — туннель закроется.
start "" http://localhost:8770
ssh -i "%USERPROFILE%\.ssh\sana_vm" -N -L 8770:127.0.0.1:8770 -o ServerAliveInterval=30 ersultan040403@34.63.192.252
