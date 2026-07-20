param(
    [string]$PythonPath = "",
    [switch]$InstallDependencies
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$PackagingDir = Join-Path $Root "packaging"
$BuildDir = Join-Path $Root "build"
$WixBuildDir = Join-Path $BuildDir "wix"
$DistDir = Join-Path $Root "dist"
$MsiPath = Join-Path $DistDir "youziauth.msi"
$Version = (Get-Content -LiteralPath (Join-Path $Root "VERSION") -Raw).Trim()
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "VERSION must use MAJOR.MINOR.PATCH"
}

function Resolve-Python {
    param([string]$RequestedPath)
    if (-not [string]::IsNullOrWhiteSpace($RequestedPath)) {
        return $RequestedPath
    }
    foreach ($candidate in @("python.exe", "python")) {
        $command = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($null -ne $command) {
            return $command.Source
        }
    }
    throw "Python was not found. Install Python 3.10+ or pass -PythonPath C:\Path\To\python.exe"
}

function Ensure-PythonBuildDependencies {
    param([string]$Python)
    $Requirements = Join-Path $Root "requirements-build.txt"
    if ($InstallDependencies) {
        & $Python -m pip install --requirement $Requirements
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to install pinned Python build dependencies"
        }
    }
    & $Python -c "import PIL, PyInstaller; assert PIL.__version__ == '12.2.0'; assert PyInstaller.__version__ == '6.16.0'"
    if ($LASTEXITCODE -ne 0) {
        throw "Pinned Python build dependencies are unavailable"
    }
}

function Resolve-Wix {
    $toolPath = Join-Path $Root ".tools"
    $localWix = Join-Path $toolPath "wix.exe"
    if (Test-Path -LiteralPath $localWix) {
        return $localWix
    }

    $command = Get-Command wix.exe -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }

    if (-not $InstallDependencies) {
        throw "WiX is not installed. Re-run with -InstallDependencies or install it with: dotnet tool install wix --tool-path .tools"
    }

    New-Item -ItemType Directory -Force -Path $toolPath | Out-Null
    dotnet tool install wix --tool-path $toolPath --version 7.0.0 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install WiX"
    }
    if (-not (Test-Path -LiteralPath $localWix)) {
        throw "WiX installation completed, but wix.exe was not found at $localWix"
    }
    return $localWix
}

$Python = Resolve-Python $PythonPath
Ensure-PythonBuildDependencies $Python
$Wix = Resolve-Wix

& $Python (Join-Path $PackagingDir "make_icons.py")
if ($LASTEXITCODE -ne 0) {
    throw "Icon generation failed"
}

& $Python (Join-Path $PackagingDir "generate_version_info.py") --output (Join-Path $BuildDir "version")
if ($LASTEXITCODE -ne 0) {
    throw "Version resource generation failed"
}

& $Python -m PyInstaller --noconfirm --clean (Join-Path $PackagingDir "youziauth.spec")
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed"
}

$AppDir = Join-Path $DistDir "youziauth"
if (-not (Test-Path -LiteralPath (Join-Path $AppDir "youziauth.exe"))) {
    throw "PyInstaller did not produce youziauth.exe in $AppDir"
}
if (-not (Test-Path -LiteralPath (Join-Path $AppDir "youziauth-agent.exe"))) {
    throw "PyInstaller did not produce youziauth-agent.exe in $AppDir"
}

New-Item -ItemType Directory -Force -Path $WixBuildDir | Out-Null
$GeneratedWxs = Join-Path $WixBuildDir "ApplicationFiles.wxs"
& $Python (Join-Path $PackagingDir "generate_wix_files.py") --app-dir $AppDir --output $GeneratedWxs
if ($LASTEXITCODE -ne 0) {
    throw "WiX file manifest generation failed"
}

& $Wix --acceptEula wix7 build `
    (Join-Path $PackagingDir "youziauth.wxs") `
    $GeneratedWxs `
    -d ProductVersion=$Version `
    -out $MsiPath
if ($LASTEXITCODE -ne 0) {
    throw "WiX MSI build failed"
}
if (-not (Test-Path -LiteralPath $MsiPath)) {
    throw "WiX completed without producing $MsiPath"
}

Write-Host "MSI created: $MsiPath"
