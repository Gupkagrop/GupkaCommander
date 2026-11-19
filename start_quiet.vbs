' GupkaCommander Silent Launcher (VBScript)
' Запускает GupkaCommander в скрытом режиме без отображения консольного окна Windows.

Set objFSO = CreateObject("Scripting.FileSystemObject")
Set objShell = CreateObject("WScript.Shell")

strScriptDir = objFSO.GetParentFolderName(WScript.ScriptFullName)
objShell.CurrentDirectory = strScriptDir

strPythonW = strScriptDir & "\.venv\Scripts\pythonw.exe"
strMainPy = strScriptDir & "\main.py"

If objFSO.FileExists(strPythonW) Then
    ' Прямой запуск через изолированный виртуальный pythonw.exe
    objShell.Run """" & strPythonW & """ """ & strMainPy & """", 0, False
Else
    ' Запуск через start.bat в скрытом окне
    objShell.Run "cmd.exe /c """ & strScriptDir & "\start.bat""", 0, False
End If
