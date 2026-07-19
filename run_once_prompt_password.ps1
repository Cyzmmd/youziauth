param(
    [string]$ConfigPath = (Join-Path $PSScriptRoot "config.ini"),
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"

$ScriptPath = Join-Path $PSScriptRoot "campus_auth.py"
if (-not (Test-Path -LiteralPath $ScriptPath)) {
    throw "Cannot find campus_auth.py at $ScriptPath"
}

if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw "Cannot find config file at $ConfigPath"
}

if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $Command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -eq $Command) {
        $Command = Get-Command python -ErrorAction SilentlyContinue
    }
    if ($null -eq $Command) {
        throw "Python was not found. Install Python 3.10+ or pass -PythonPath C:\Path\To\python.exe"
    }
    $PythonPath = $Command.Source
}

$SecurePassword = Read-Host "Campus network password" -AsSecureString
$PlainPassword = [System.Net.NetworkCredential]::new("", $SecurePassword).Password

try {
    $env:CAMPUS_AUTH_PASSWORD = $PlainPassword
    & $PythonPath $ScriptPath --config $ConfigPath --once --verbose
    exit $LASTEXITCODE
}
finally {
    Remove-Item Env:\CAMPUS_AUTH_PASSWORD -ErrorAction SilentlyContinue
    $PlainPassword = $null
    $SecurePassword.Dispose()
}
