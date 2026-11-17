' ============================================================================
'  Run GAP Pricing Engine (silent)
'  Launches the app with NO console window using pythonw.exe.
'  Keep this .vbs in the repository ROOT (next to BackendEngine\).
'  Prefers the repo virtual environment at .venv\ when present.
' ============================================================================

Option Explicit

Dim fso, shell, scriptDir, engineDir, pyw, cmd
Set fso   = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
engineDir = scriptDir & "\BackendEngine"

' Prefer the repo virtual environment's pythonw, then the windowed
' launcher 'pyw', then pythonw on PATH.
If fso.FileExists(scriptDir & "\.venv\Scripts\pythonw.exe") Then
    pyw = """" & scriptDir & "\.venv\Scripts\pythonw.exe" & """"
Else
    pyw = "pyw"
    On Error Resume Next
    shell.Run "pyw --version", 0, True
    If Err.Number <> 0 Then
        pyw = "pythonw"
    End If
    On Error Goto 0
End If

cmd = pyw & " -B -m gap_pricing_gui.launch_gap_gui"

' 0 = hidden window, False = don't wait
shell.CurrentDirectory = engineDir
shell.Run cmd, 0, False
