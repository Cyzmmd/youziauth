param(
    [string]$TaskName = "youziauth",
    [string]$PythonPath = "",
    [string]$ConfigPath = (Join-Path $PSScriptRoot "config.ini")
)

$ErrorActionPreference = "Stop"

$ScriptPath = Join-Path $PSScriptRoot "campus_auth.py"
$RunnerPath = Join-Path $PSScriptRoot "run_with_saved_password.ps1"
if (-not (Test-Path -LiteralPath $ScriptPath)) {
    throw "Cannot find campus_auth.py at $ScriptPath"
}

if (-not (Test-Path -LiteralPath $RunnerPath)) {
    throw "Cannot find run_with_saved_password.ps1 at $RunnerPath"
}

if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw "Cannot find config file at $ConfigPath. Copy config.example.ini to config.ini and edit it first."
}

if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $Command = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($null -eq $Command) {
        $Command = Get-Command python.exe -ErrorAction SilentlyContinue
    }
    if ($null -eq $Command) {
        $Command = Get-Command python -ErrorAction SilentlyContinue
    }
    if ($null -eq $Command) {
        throw "Python was not found. Install Python 3.10+ or pass -PythonPath C:\Path\To\pythonw.exe"
    }
    $PythonPath = $Command.Source
}

function Install-StartupShortcut {
    param(
        [string]$ShortcutName,
        [string]$RunnerPath,
        [string]$ConfigPath,
        [string]$PythonPath
    )

    $StartupDir = [Environment]::GetFolderPath("Startup")
    if ([string]::IsNullOrWhiteSpace($StartupDir)) {
        throw "Cannot find the current user's Startup folder."
    }

    $ShortcutPath = Join-Path $StartupDir "$ShortcutName.lnk"
    $Shell = New-Object -ComObject WScript.Shell
    $Shortcut = $Shell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = "powershell.exe"
    $Shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$RunnerPath`" -ConfigPath `"$ConfigPath`" -PythonPath `"$PythonPath`""
    $Shortcut.WorkingDirectory = $PSScriptRoot
    $Shortcut.IconLocation = "powershell.exe,0"
    $Shortcut.Description = "Keep youziauth authenticated through the ePortal login endpoint."
    $Shortcut.Save()

    Write-Host "Startup shortcut '$ShortcutName' has been installed."
    Write-Host "Shortcut: $ShortcutPath"
}

$Argument = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$RunnerPath`" -ConfigPath `"$ConfigPath`" -PythonPath `"$PythonPath`""
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $Argument
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -Description "Keep youziauth authenticated through the ePortal login endpoint." `
        -Force | Out-Null

    Write-Host "Scheduled task '$TaskName' has been installed."
}
catch {
    $ErrorText = $_ | Out-String
    if ($ErrorText -notmatch "0x80070005|拒绝访问|Access is denied|Access denied") {
        throw
    }

    Write-Warning "Windows denied permission to register a scheduled task. Falling back to a per-user Startup shortcut."
    Install-StartupShortcut `
        -ShortcutName $TaskName `
        -RunnerPath $RunnerPath `
        -ConfigPath $ConfigPath `
        -PythonPath $PythonPath
}

Write-Host "Python: $PythonPath"
Write-Host "Runner: $RunnerPath"
Write-Host "Config: $ConfigPath"
