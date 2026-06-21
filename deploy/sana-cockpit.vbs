' ============================================================
' Sana Corp — командный центр при старте Windows.
' Открывает: Trello-доску + Telegram + живой офис (через SSH-туннель к VM).
' Туннель поднимается СКРЫТО (без окна). Положить в автозагрузку (shell:startup).
' ============================================================
Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' 1) Trello-доска
sh.Run "https://trello.com/b/evNE5HfJ", 1, False

' 2) Telegram (десктоп если установлен, иначе веб)
tg = sh.ExpandEnvironmentStrings("%APPDATA%\Telegram Desktop\Telegram.exe")
If fso.FileExists(tg) Then
    sh.Run """" & tg & """", 1, False
Else
    sh.Run "https://web.telegram.org", 1, False
End If

' 3) SSH-туннель к VM (скрыто, 0 = без окна)
key = sh.ExpandEnvironmentStrings("%USERPROFILE%\.ssh\sana_vm")
sh.Run "ssh -i """ & key & """ -N -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -L 8770:127.0.0.1:8770 ersultan040403@34.63.192.252", 0, False

' 4) Живой офис (после паузы, чтобы туннель успел подняться)
WScript.Sleep 4500
sh.Run "http://localhost:8770", 1, False
