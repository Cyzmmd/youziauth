Option Explicit

Dim shell, fso, root, pythonw, script, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

root = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = "pythonw.exe"
If fso.FileExists(fso.BuildPath(root, ".tools\desktop-python\Scripts\pythonw.exe")) Then
    pythonw = fso.BuildPath(root, ".tools\desktop-python\Scripts\pythonw.exe")
End If
script = fso.BuildPath(root, "campus_auth_gui.py")
command = """" & pythonw & """ """ & script & """"

shell.Run command, 1, False
