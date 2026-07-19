param(
    [string]$PasswordFile = (Join-Path $PSScriptRoot "campus_auth_password.txt")
)

$ErrorActionPreference = "Stop"

$SecurePassword = Read-Host "Campus network password" -AsSecureString
$EncryptedPassword = $SecurePassword | ConvertFrom-SecureString
Set-Content -LiteralPath $PasswordFile -Value $EncryptedPassword -Encoding ASCII -NoNewline

Write-Host "Encrypted password saved to $PasswordFile"
Write-Host "It can only be decrypted by the same Windows user on this machine."
