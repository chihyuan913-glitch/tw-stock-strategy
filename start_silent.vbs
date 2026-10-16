Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
currentDir = FSO.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = currentDir

portablePy = currentDir & "\runtime\python.exe"
pyPath = "C:\Users\User.DESKTOP-OI789R1\AppData\Local\Programs\Python\Python313\python.exe"

If FSO.FileExists(portablePy) Then
    WshShell.Run """" & portablePy & """ """ & currentDir & "\start_web.py""", 0, False
ElseIf FSO.FileExists(pyPath) Then
    WshShell.Run """" & pyPath & """ """ & currentDir & "\start_web.py""", 0, False
Else
    WshShell.Run "python """ & currentDir & "\start_web.py""", 0, False
End If
