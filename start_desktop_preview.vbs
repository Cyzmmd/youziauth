Option Explicit
Dim shell, fso, root, target, command, q
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
q = Chr(34)
target = fso.BuildPath(root, "dist\youziauth\youziauth.exe")
If fso.FileExists(target) Then
    command = q & target & q & " --preview"
Else
    target = fso.BuildPath(root, ".tools\desktop-python\Scripts\pythonw.exe")
    If Not fso.FileExists(target) Then target = "pythonw.exe"
    command = q & target & q & " " & q & fso.BuildPath(root, "campus_auth_desktop.py") & q & " --preview"
End If
shell.CurrentDirectory = root
shell.Run command, 1, False
