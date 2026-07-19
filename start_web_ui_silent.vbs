Option Explicit

Dim shell, fso, root, pythonw, script, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

root = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = "pythonw.exe"
script = fso.BuildPath(root, "campus_auth_web.py")
command = """" & pythonw & """ """ & script & """ --host 127.0.0.1 --port 8765"

shell.Run command, 0, False
WScript.Sleep 1000
shell.Run "http://127.0.0.1:8765/", 1, False
