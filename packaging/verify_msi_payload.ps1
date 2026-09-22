param(
    [Parameter(Mandatory = $true)][string]$MsiPath,
    [Parameter(Mandatory = $true)][string]$AppDir,
    [string]$WorkDirectory = ""
)

$ErrorActionPreference = "Stop"

# The 1.4.0 package shipped 41 payload files that Windows Installer deferred to the
# next reboot because the tray application still held them open. The application then
# failed to start with "Failed to start embedded python interpreter: Failed to import
# encodings module" because _internal\base_library.zip was among the deferred files.
# Building the MSI is not proof that the MSI carries the payload, so extract the
# package and compare it against the bundle PyInstaller produced.

$MsiPath = (Resolve-Path -LiteralPath $MsiPath).Path
$AppDir = (Resolve-Path -LiteralPath $AppDir).Path

if ([string]::IsNullOrWhiteSpace($WorkDirectory)) {
    $WorkDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ("youziauth-payload-" + [guid]::NewGuid().ToString("N"))
}
New-Item -ItemType Directory -Force -Path $WorkDirectory | Out-Null
$WorkDirectory = (Resolve-Path -LiteralPath $WorkDirectory).Path
$SafePrefix = $AppDir.TrimEnd('\') + [System.IO.Path]::DirectorySeparatorChar
if ($WorkDirectory.StartsWith($SafePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to extract the package inside the bundle directory $AppDir"
}

try {
    $Extract = Join-Path $WorkDirectory "extract"
    $Log = Join-Path $WorkDirectory "extract.log"
    $Process = Start-Process msiexec.exe -ArgumentList @(
        '/a',
        ('"' + $MsiPath + '"'),
        '/qn',
        ('TARGETDIR="' + $Extract + '"'),
        '/L*v',
        ('"' + $Log + '"')
    ) -Wait -PassThru
    if ($Process.ExitCode -ne 0) {
        throw "MSI administrative extraction failed: $($Process.ExitCode) (see $Log)"
    }

    $Root = Join-Path $Extract "PFiles\youziauth"
    if (-not (Test-Path -LiteralPath $Root)) {
        throw "Administrative extraction did not produce $Root"
    }

    $Extracted = @{}
    foreach ($File in Get-ChildItem -LiteralPath $Root -Recurse -File -Force) {
        $Extracted[$File.FullName.Substring($Root.Length + 1)] = $File.Length
    }

    $Missing = New-Object System.Collections.Generic.List[string]
    $Mismatched = New-Object System.Collections.Generic.List[string]
    $Expected = 0
    foreach ($File in Get-ChildItem -LiteralPath $AppDir -Recurse -File -Force) {
        $Relative = $File.FullName.Substring($AppDir.Length + 1)
        if ($Relative -like ".msi-payload-verify-*") {
            continue
        }
        $Expected++
        if (-not $Extracted.ContainsKey($Relative)) {
            $Missing.Add($Relative)
        }
        elseif ($Extracted[$Relative] -ne $File.Length) {
            $Mismatched.Add("$Relative (bundle=$($File.Length) msi=$($Extracted[$Relative]))")
        }
    }
    $Unexpected = @($Extracted.Keys | Where-Object { -not (Test-Path -LiteralPath (Join-Path $AppDir $_)) })

    if ($Missing.Count -gt 0) {
        Write-Host "Payload files missing from the MSI package ($($Missing.Count)):"
        $Missing | Select-Object -First 20 | ForEach-Object { Write-Host "  MISSING  $_" }
    }
    if ($Mismatched.Count -gt 0) {
        Write-Host "Payload files with a size mismatch ($($Mismatched.Count)):"
        $Mismatched | Select-Object -First 20 | ForEach-Object { Write-Host "  SIZE     $_" }
    }
    if ($Unexpected.Count -gt 0) {
        Write-Host "Package files that are absent from the bundle ($($Unexpected.Count)):"
        $Unexpected | Select-Object -First 20 | ForEach-Object { Write-Host "  EXTRA    $_" }
    }

    if ($Missing.Count -gt 0 -or $Mismatched.Count -gt 0 -or $Unexpected.Count -gt 0) {
        throw "MSI payload does not match the PyInstaller bundle"
    }

    Write-Host "MSI payload verified: $Expected files match $AppDir"
}
finally {
    if (Test-Path -LiteralPath $WorkDirectory) {
        $Resolved = (Resolve-Path -LiteralPath $WorkDirectory).Path
        if ($Resolved.StartsWith($SafePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove a work directory inside the bundle directory $AppDir"
        }
        Remove-Item -LiteralPath $Resolved -Recurse -Force -ErrorAction SilentlyContinue
    }
}
