Option Explicit

Dim shell, fso, root, runner, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

root = fso.GetParentFolderName(WScript.ScriptFullName)
runner = fso.BuildPath(root, "run_with_saved_password.ps1")
command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File " & """" & runner & """"

shell.Run command, 0, False
