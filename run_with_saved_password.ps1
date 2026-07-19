param(
    [string]$ConfigPath = (Join-Path $PSScriptRoot "config.ini"),
    [string]$PasswordFile = (Join-Path $PSScriptRoot "campus_auth_password.txt"),
    [string]$PythonPath = "",
    [switch]$Once,
    [switch]$VerboseAuth
)

$ErrorActionPreference = "Stop"

$ScriptPath = Join-Path $PSScriptRoot "campus_auth.py"
if (-not (Test-Path -LiteralPath $ScriptPath)) {
    throw "Cannot find campus_auth.py at $ScriptPath"
}

if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw "Cannot find config file at $ConfigPath"
}

if (-not (Test-Path -LiteralPath $PasswordFile)) {
    throw "Cannot find password file at $PasswordFile. Run save_password.ps1 first."
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
        throw "Python was not found. Install Python 3.10+ or pass -PythonPath C:\Path\To\python.exe"
    }
    $PythonPath = $Command.Source
}

$EncryptedPassword = (Get-Content -LiteralPath $PasswordFile -Raw).Trim()
$SecurePassword = $EncryptedPassword | ConvertTo-SecureString
$PlainPassword = [System.Net.NetworkCredential]::new("", $SecurePassword).Password

try {
    $env:CAMPUS_AUTH_PASSWORD = $PlainPassword
    $Arguments = @($ScriptPath, "--config", $ConfigPath)
    if ($Once) {
        $Arguments += "--once"
    }
    if ($VerboseAuth) {
        $Arguments += "--verbose"
    }
    & $PythonPath @Arguments
    exit $LASTEXITCODE
}
finally {
    Remove-Item Env:\CAMPUS_AUTH_PASSWORD -ErrorAction SilentlyContinue
    $PlainPassword = $null
    $SecurePassword.Dispose()
}
