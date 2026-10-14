Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
currentDir = FSO.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = currentDir

pyPath = "C:\Users\User.DESKTOP-OI789R1\AppData\Local\Programs\Python\Python313\python.exe"
If FSO.FileExists(pyPath) Then
    WshShell.Run """" & pyPath & """ """ & currentDir & "\run_line_bot.py""", 0, False
Else
    WshShell.Run "python """ & currentDir & "\run_line_bot.py""", 0, False
End If
