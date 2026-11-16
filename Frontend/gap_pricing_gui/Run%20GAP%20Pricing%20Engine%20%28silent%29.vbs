' ============================================================================
'  Run GAP Pricing Engine (silent)
'  Launches the app with NO console window using pythonw.exe.
'  Keep this .vbs in the SAME folder as launch_gap_gui.py.
'  Errors are still captured in launch_gap_gui.log by the Python launcher.
' ============================================================================

Option Explicit

Dim fso, shell, scriptDir, launcher, pyw, cmd
Set fso   = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
launcher  = scriptDir & "\launch_gap_gui.py"

' Prefer the windowed launcher 'pyw' (installed with the Python launcher),
' fall back to pythonw on PATH.
pyw = "pyw"
On Error Resume Next
shell.Run "pyw --version", 0, True
If Err.Number <> 0 Then
    pyw = "pythonw"
End If
On Error Goto 0

cmd = pyw & " -m gap_pricing_gui.launch_gap_gui"

' 0 = hidden window, False = don't wait
shell.CurrentDirectory = fso.GetParentFolderName(scriptDir)
shell.Run cmd, 0, False
