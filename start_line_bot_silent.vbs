Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
currentDir = FSO.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = currentDir

portablePy = currentDir & "\runtime\python.exe"
pyPath = "C:\Users\User.DESKTOP-OI789R1\AppData\Local\Programs\Python\Python313\python.exe"

If FSO.FileExists(portablePy) Then
    WshShell.Run """" & portablePy & """ """ & currentDir & "\run_line_bot.py""", 0, False
ElseIf FSO.FileExists(pyPath) Then
    WshShell.Run """" & pyPath & """ """ & currentDir & "\run_line_bot.py""", 0, False
Else
    WshShell.Run "python """ & currentDir & "\run_line_bot.py""", 0, False
End If
