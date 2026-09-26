param(
    [Parameter(Mandatory = $true)][string]$MsiPath,
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$OutputDirectory
)

# Verifies that the MSI really contains the two application executables at the
# expected version, and that the detached Ed25519 authenticators match its bytes.
# The signature check is the release gate; tools/sign_release.py refuses to sign
# with a key that does not match the public key compiled into the client.

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$MsiPath = (Resolve-Path -LiteralPath $MsiPath).Path
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$OutputDirectory = (Resolve-Path -LiteralPath $OutputDirectory).Path

# The published signature must genuinely cover these installer bytes. The check
# uses the public key compiled into the client, so it needs no private key.
$SignaturePath = Join-Path $OutputDirectory 'youziauth.msi.ed25519'
if (-not (Test-Path -LiteralPath $SignaturePath)) {
    $SignaturePath = Join-Path (Split-Path -Parent $MsiPath) 'youziauth.msi.ed25519'
}
if (-not (Test-Path -LiteralPath $SignaturePath)) {
    throw "Missing Ed25519 signature for $MsiPath"
}
& python (Join-Path $Root 'tools/sign_release.py') --msi $MsiPath --version $Version `
    --verify-signature-file $SignaturePath
if ($LASTEXITCODE -ne 0) {
    throw "Published Ed25519 signature does not cover the installer bytes"
}

$TempRoot = $env:RUNNER_TEMP
if ([string]::IsNullOrWhiteSpace($TempRoot)) {
    $TempRoot = [System.IO.Path]::GetTempPath()
}
$TempRoot = (Resolve-Path -LiteralPath $TempRoot).Path.TrimEnd('\')
$Extract = Join-Path $TempRoot ("youziauth-msi-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $Extract | Out-Null
$Extract = (Resolve-Path -LiteralPath $Extract).Path
$SafePrefix = $TempRoot + [System.IO.Path]::DirectorySeparatorChar
if (-not $Extract.StartsWith($SafePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to use extraction directory outside the runner temp path"
}

try {
    $Process = Start-Process msiexec.exe -ArgumentList @(
        '/a',
        ('"' + $MsiPath + '"'),
        '/qn',
        ('TARGETDIR="' + $Extract + '"')
    ) -Wait -PassThru
    if ($Process.ExitCode -ne 0) {
        throw "MSI administrative extraction failed: $($Process.ExitCode)"
    }

    $Executables = @(
        Get-ChildItem -LiteralPath $Extract -Recurse -File |
            Where-Object Name -in @('youziauth.exe', 'youziauth-agent.exe')
    )
    if ($Executables.Count -ne 2) {
        throw "Expected two application executables in the package"
    }
    foreach ($File in $Executables) {
        if ($File.VersionInfo.FileVersion -notin @($Version, "$Version.0")) {
            throw "$($File.Name) FileVersion mismatch"
        }
        if ($File.VersionInfo.ProductVersion -notin @($Version, "$Version.0")) {
            throw "$($File.Name) ProductVersion mismatch"
        }
        if ($File.VersionInfo.CompanyName -ne 'yoouzic') {
            throw "$($File.Name) CompanyName mismatch"
        }
        if ($File.VersionInfo.ProductName -ne 'youziauth') {
            throw "$($File.Name) ProductName mismatch"
        }
    }

    $Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $MsiPath).Hash.ToLowerInvariant()
    "$Hash  youziauth.msi" | Set-Content -LiteralPath (Join-Path $OutputDirectory 'SHA256SUMS.txt') -Encoding ascii
    Write-Host "Verified youziauth.msi $Version ($Hash)"
}
finally {
    if (Test-Path -LiteralPath $Extract) {
        $ResolvedExtract = (Resolve-Path -LiteralPath $Extract).Path
        if (-not $ResolvedExtract.StartsWith($SafePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove extraction directory outside the runner temp path"
        }
        Remove-Item -LiteralPath $ResolvedExtract -Recurse -Force
    }
}
