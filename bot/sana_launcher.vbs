' Запускает watchdog бота скрыто (без чёрного окна). Вызывается Планировщиком при входе.
Set sh = CreateObject("WScript.Shell")
sh.Run "cmd /c ""C:\Users\Acer\AI_Assistant\projects\jarvis\bot\sana_watchdog.bat""", 0, False
