Option Explicit

Dim shell, fso, root, pythonw, script, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

root = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = "pythonw.exe"
script = fso.BuildPath(root, "campus_auth_gui.py")
command = """" & pythonw & """ """ & script & """"

shell.Run command, 0, False
