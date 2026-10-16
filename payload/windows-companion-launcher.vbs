Option Explicit
Dim shell, files, scriptPath, command
If WScript.Arguments.Count <> 1 Then
  WScript.Echo "PLAN: companion monitor stays stopped. Supply --apply to launch the configured sibling PowerShell wrapper."
  WScript.Quit 0
End If
If WScript.Arguments(0) <> "--apply" Then
  WScript.Echo "PLAN: expected --apply; no process started."
  WScript.Quit 0
End If
Set files = CreateObject("Scripting.FileSystemObject")
scriptPath = files.BuildPath(files.GetParentFolderName(WScript.ScriptFullName), "windows-companion-launcher.ps1")
If Not files.FileExists(scriptPath) Then
  WScript.Echo "Companion script missing."
  WScript.Quit 2
End If
Set shell = CreateObject("WScript.Shell")
command = "powershell.exe -NoProfile -File """ & scriptPath & """ -Apply"
shell.Run command, 0, False
