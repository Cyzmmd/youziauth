param(
    [Parameter(Mandatory = $true)][string]$MsiPath,
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$OutputDirectory
)

$ErrorActionPreference = "Stop"

$MsiPath = (Resolve-Path -LiteralPath $MsiPath).Path
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$OutputDirectory = (Resolve-Path -LiteralPath $OutputDirectory).Path

$msiSignature = Get-AuthenticodeSignature -LiteralPath $MsiPath
if ($msiSignature.Status -ne 'Valid') {
    throw "MSI signature is $($msiSignature.Status)"
}
if ($null -eq $msiSignature.TimeStamperCertificate) {
    throw "MSI signature has no trusted timestamp"
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
        throw "Expected two signed application executables"
    }
    foreach ($File in $Executables) {
        $Signature = Get-AuthenticodeSignature -LiteralPath $File.FullName
        if ($Signature.Status -ne 'Valid') {
            throw "$($File.Name) signature is $($Signature.Status)"
        }
        if ($null -eq $Signature.TimeStamperCertificate) {
            throw "$($File.Name) signature has no trusted timestamp"
        }
        if ($File.VersionInfo.FileVersion -notin @($Version, "$Version.0")) {
            throw "$($File.Name) FileVersion mismatch"
        }
        if ($File.VersionInfo.ProductVersion -notin @($Version, "$Version.0")) {
            throw "$($File.Name) ProductVersion mismatch"
        }
    }

    $Hash = Get-FileHash -Algorithm SHA256 -LiteralPath $MsiPath
    "$($Hash.Hash)  youziauth.msi" |
        Set-Content -LiteralPath (Join-Path $OutputDirectory "SHA256SUMS.txt") -Encoding ascii
    [ordered]@{
        version = $Version
        git_commit = $env:GITHUB_SHA
        git_tag = $env:GITHUB_REF_NAME
        msi_sha256 = $Hash.Hash
        signer_subject = $msiSignature.SignerCertificate.Subject
        timestamp_subject = $msiSignature.TimeStamperCertificate.Subject
    } | ConvertTo-Json |
        Set-Content -LiteralPath (Join-Path $OutputDirectory "release-provenance.json") -Encoding utf8
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
