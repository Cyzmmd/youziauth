# Copyright (C) 2026 yoouzic
# SPDX-License-Identifier: GPL-3.0-only

"""Verify updates against the running signed application before interactive installation."""

from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path


class UpdateVerificationError(RuntimeError):
    """An update could not be safely verified, launched, or observed."""


_OFFICIAL = "无法确认当前程序的可信签名，请使用官方签名安装版。"
_VERIFICATION_FAILED = "无法完成更新安全验证，请稍后重试。"
_ERRORS = {
    10: _OFFICIAL,
    11: "MSI 安装包未签名或签名无效，已拒绝更新。",
    12: "MSI 安装包缺少可信签名时间戳，已拒绝更新。",
    13: "MSI 安装包与当前程序的签名发布者不一致，已拒绝更新。",
    14: "无法只读检查 MSI 产品信息，已拒绝更新。",
    15: "MSI 安装包 SHA256 哈希不匹配或无法读取，已拒绝更新。",
    16: "MSI 产品名称、制造商、版本或升级标识不匹配，已拒绝更新。",
    17: "无法锁定 MSI 安装包，已取消更新。",
    18: "更新辅助进程未能安全独立启动或启动超时，已取消更新。",
    19: "无法启动 Windows MSI 安装向导，请稍后重试。",
    20: "无法获取安装向导结果，请检查安装向导状态。",
    21: _VERIFICATION_FAILED,
    22: "更新安全验证超时，请稍后重试。",
}
_UPGRADE_CODE = "{D029E636-7E7E-42EE-8B38-C2D455AD2AA1}"

# Signature gates must precede Windows Installer COM access; paths never become script source.
_POWERSHELL = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$stage = 10
try {
    $exe = Microsoft.PowerShell.Security\Get-AuthenticodeSignature -LiteralPath $env:YOUZIAUTH_UPDATE_EXE
    if ($exe.Status.ToString() -cne 'Valid' -or $null -eq $exe.SignerCertificate -or
        [string]::IsNullOrWhiteSpace($exe.SignerCertificate.Subject)) { exit 10 }
    $stage = 11
    $msi = Microsoft.PowerShell.Security\Get-AuthenticodeSignature -LiteralPath $env:YOUZIAUTH_UPDATE_MSI
    if ($msi.Status.ToString() -cne 'Valid' -or $null -eq $msi.SignerCertificate) { exit 11 }
    if ($null -eq $msi.TimeStamperCertificate) { exit 12 }
    if (-not [string]::Equals($exe.SignerCertificate.Subject, $msi.SignerCertificate.Subject,
                             [System.StringComparison]::Ordinal)) { exit 13 }

    $stage = 14
    $installer = $null
    $database = $null
    $view = $null
    $record = $null
    $properties = @{}
    $names = @('ProductName', 'Manufacturer', 'ProductVersion', 'UpgradeCode')
    $method = [System.Reflection.BindingFlags]::InvokeMethod
    $property = [System.Reflection.BindingFlags]::GetProperty
    try {
        $installer = New-Object -ComObject WindowsInstaller.Installer
        # 0 = msiOpenDatabaseModeReadOnly. No install, extraction, or actions.
        $database = $installer.GetType().InvokeMember('OpenDatabase', $method, $null,
                                                       $installer, @($env:YOUZIAUTH_UPDATE_MSI, 0))
        $view = $database.GetType().InvokeMember('OpenView', $method, $null, $database,
                                                @('SELECT `Property`, `Value` FROM `Property`'))
        $null = $view.GetType().InvokeMember('Execute', $method, $null, $view, $null)
        while ($true) {
            $record = $view.GetType().InvokeMember('Fetch', $method, $null, $view, $null)
            if ($null -eq $record) { break }
            try {
                $name = [string]$record.GetType().InvokeMember('StringData', $property, $null, $record, @(1))
                if ($names -ccontains $name) {
                    if ($properties.ContainsKey($name)) { throw 'duplicate-property' }
                    $properties[$name] = [string]$record.GetType().InvokeMember('StringData', $property,
                                                                                $null, $record, @(2))
                }
            } finally {
                $null = [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($record)
                $record = $null
            }
        }
    } finally {
        if ($null -ne $view) {
            try { $null = $view.GetType().InvokeMember('Close', $method, $null, $view, $null) }
            finally { $null = [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($view) }
        }
        if ($null -ne $database) { $null = [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($database) }
        if ($null -ne $installer) { $null = [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($installer) }
    }
    $result = @{
        exe = @{ status = $exe.Status.ToString(); subject = $exe.SignerCertificate.Subject }
        msi = @{ status = $msi.Status.ToString(); subject = $msi.SignerCertificate.Subject; timestamp = $true }
        properties = $properties
    }
    [Console]::Out.Write(($result | ConvertTo-Json -Depth 4 -Compress))
    exit 0
} catch {
    # Never emit exception details, OS paths, or the original script.
    exit $stage
}
"""
_ENCODED_COMMAND = base64.b64encode(_POWERSHELL.encode("utf-16le")).decode("ascii")

_WORKER = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$directory = $null
$lock = $null
$installer = $null
$stage = 18
$final = @{ error = 18 }
function Publish-Status($name, $value) {
    $target = [IO.Path]::Combine($directory, $name + '.json')
    [IO.File]::WriteAllText($target + '.tmp', ($value | ConvertTo-Json -Compress),
                           (New-Object Text.UTF8Encoding($false)))
    [IO.File]::Move($target + '.tmp', $target)
}
try {
    $request = [Text.Encoding]::UTF8.GetString(
        [Convert]::FromBase64String($env:YOUZIAUTH_UPDATE_REQUEST)) | ConvertFrom-Json
    $directory = $request.directory
    $launcher = $null
    try { $launcher = [Diagnostics.Process]::GetProcessById([int]$env:YOUZIAUTH_UPDATE_LAUNCHER) }
    catch [ArgumentException] { }
    if ($null -ne $launcher) {
        try {
            if (-not $launcher.WaitForExit(15000)) { throw 'launcher-alive' }
        } finally { $launcher.Dispose() }
    }

    $stage = 17
    $lock = [IO.File]::Open($env:YOUZIAUTH_UPDATE_MSI, [IO.FileMode]::Open,
                           [IO.FileAccess]::Read, [IO.FileShare]::Read)
    $stage = 15
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { $digest = [BitConverter]::ToString($hasher.ComputeHash($lock)).Replace('-', '').ToLowerInvariant() }
    finally { $hasher.Dispose() }
    if ($digest -cne $request.sha256) { throw 'hash' }

    $stage = 21
    $powershell = [IO.Path]::Combine([Environment]::SystemDirectory, 'WindowsPowerShell\v1.0\powershell.exe')
    $check = New-Object Diagnostics.ProcessStartInfo
    $check.FileName = $powershell
    $check.Arguments = '-NoProfile -NonInteractive -EncodedCommand ' + $env:YOUZIAUTH_UPDATE_VERIFY
    $check.UseShellExecute = $false
    $check.CreateNoWindow = $true
    $check.RedirectStandardOutput = $true
    $check.RedirectStandardError = $true
    $check.StandardOutputEncoding = New-Object Text.UTF8Encoding($false)
    $verifier = [Diagnostics.Process]::Start($check)
    try {
        $output = $verifier.StandardOutput.ReadToEndAsync()
        $errors = $verifier.StandardError.ReadToEndAsync()
        $stage = 22
        if (-not $verifier.WaitForExit(60000)) { throw 'verification-timeout' }
        $stage = 21
        if ($verifier.ExitCode -ne 0) {
            if ($verifier.ExitCode -ge 10 -and $verifier.ExitCode -le 14) { $stage = $verifier.ExitCode }
            throw 'verification'
        }
        $result = $output.Result | ConvertFrom-Json
    } finally { $verifier.Dispose() }

    $stage = 10
    if ($result.exe.status -isnot [string] -or $result.exe.status -cne 'Valid' -or
        $result.exe.subject -isnot [string] -or
        [string]::IsNullOrWhiteSpace($result.exe.subject)) { throw 'exe-signature' }
    $stage = 11
    if ($result.msi.status -isnot [string] -or $result.msi.status -cne 'Valid') { throw 'msi-signature' }
    $stage = 12
    if ($result.msi.timestamp -isnot [bool] -or -not $result.msi.timestamp) { throw 'timestamp' }
    $stage = 13
    if ($result.msi.subject -isnot [string] -or
        -not [string]::Equals($result.exe.subject, $result.msi.subject,
                             [StringComparison]::Ordinal)) { throw 'publisher' }
    $stage = 16
    if ($result.properties -isnot [Management.Automation.PSCustomObject]) { throw 'properties' }
    foreach ($property in $request.properties.PSObject.Properties) {
        $actual = $result.properties.PSObject.Properties[$property.Name]
        if ($null -eq $actual -or $actual.Value -isnot [string] -or
            $actual.Value -cne $property.Value) { throw 'property' }
    }

    $stage = 18
    if ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() -ge $request.deadline) { throw 'startup-timeout' }
    $stage = 19
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = [IO.Path]::Combine([Environment]::SystemDirectory, 'msiexec.exe')
    $start.Arguments = '/i "' + $env:YOUZIAUTH_UPDATE_MSI + '" /norestart'
    $start.UseShellExecute = $false
    $installer = [Diagnostics.Process]::Start($start)
    if ($null -eq $installer) { throw 'installer-start' }
    $stage = 20
    try { Publish-Status 'launch' @{ pid = $installer.Id } }
    finally { $installer.WaitForExit() }
    $final = @{ code = $installer.ExitCode }
} catch {
    $final = @{ error = $stage }
} finally {
    if ($null -ne $installer) { $installer.Dispose() }
    if ($null -ne $lock) { $lock.Dispose() }
}
try { Publish-Status 'final' $final }
catch { exit 1 }
"""
_ENCODED_WORKER = base64.b64encode(_WORKER.encode("utf-16le")).decode("ascii")

_LAUNCHER = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
try {
    $env:YOUZIAUTH_UPDATE_LAUNCHER = [string]$PID
    $powershell = [IO.Path]::Combine([Environment]::SystemDirectory, 'WindowsPowerShell\v1.0\powershell.exe')
    $worker = Start-Process -FilePath $powershell -WindowStyle Hidden -PassThru -ArgumentList @(
        '-NoProfile', '-NonInteractive', '-EncodedCommand', $env:YOUZIAUTH_UPDATE_WORKER)
    [Console]::Out.Write((@{ pid = $worker.Id } | ConvertTo-Json -Compress))
    $worker.Dispose()
} catch { exit 1 }
"""
_ENCODED_LAUNCHER = base64.b64encode(_LAUNCHER.encode("utf-16le")).decode("ascii")


def _kernel32():
    if sys.platform != "win32":
        raise UpdateVerificationError("更新安装仅支持 Windows。")
    try:
        # kernel32 is a Windows KnownDLL, not a PATH-resolved executable.
        api = ctypes.WinDLL("kernel32.dll", use_last_error=True)
        api.GetSystemDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
        api.GetSystemDirectoryW.restype = wintypes.UINT
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        api.WaitForSingleObject.restype = wintypes.DWORD
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        api.CloseHandle.restype = wintypes.BOOL
        return api
    except (OSError, AttributeError, TypeError, ValueError):
        raise UpdateVerificationError("无法使用 Windows 更新安全接口。") from None


def _system_tool(relative_path: str) -> Path:
    api = _kernel32()
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        length = api.GetSystemDirectoryW(buffer, len(buffer))
        if not 0 < length < len(buffer) or not Path(buffer.value).is_absolute():
            raise ValueError
        return Path(buffer.value) / relative_path
    except (OSError, ValueError):
        raise UpdateVerificationError("无法定位 Windows 系统更新工具。") from None


def _file_path(value: Path, message: str) -> Path:
    try:
        if not isinstance(value, Path):
            raise ValueError
        raw = str(value)
        # Refuse control characters, network/device namespaces and ADS paths.
        if (any(ord(char) < 32 for char in raw) or raw.startswith(("\\\\", "//"))
                or ":" in os.path.splitdrive(raw)[1]):
            raise ValueError
        resolved = value.resolve(strict=True)
        if not resolved.is_file() or str(resolved).startswith(("\\\\", "//")):
            raise ValueError
        return resolved
    except (OSError, ValueError, RuntimeError):
        raise UpdateVerificationError(message) from None


def _validate_inputs(path: Path, executable: Path | None, version: str, sha256: str) -> tuple[Path, Path]:
    if sys.platform != "win32":
        raise UpdateVerificationError("更新安装仅支持 Windows。")
    if not isinstance(version, str) or re.fullmatch(
        r"(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,4})", version
    ) is None:
        raise UpdateVerificationError("更新版本必须为严格的 MAJOR.MINOR.PATCH 格式。")
    if any(part > limit for part, limit in zip(map(int, version.split(".")), (255, 255, 65535))):
        raise UpdateVerificationError("更新版本超出 Windows MSI 支持的范围。")
    if not isinstance(sha256, str) or re.fullmatch(r"[0-9a-fA-F]{64}", sha256) is None:
        raise UpdateVerificationError("更新 SHA256 哈希格式无效。")
    if not getattr(sys, "frozen", False):
        raise UpdateVerificationError(_OFFICIAL)
    current = _file_path(Path(sys.executable), _OFFICIAL)
    anchor = current if executable is None else _file_path(executable, _OFFICIAL)
    if current.name.casefold() != "youziauth.exe" or anchor != current:
        raise UpdateVerificationError(_OFFICIAL)
    msi = _file_path(path, "MSI 安装包路径无效或文件不可读取。")
    if msi.suffix.lower() != ".msi":
        raise UpdateVerificationError("更新安装包必须为 MSI 文件。")
    return msi, anchor


def _powershell_result(path: Path, executable: Path) -> object:
    powershell = _system_tool("WindowsPowerShell/v1.0/powershell.exe")
    environment = os.environ.copy()
    environment["YOUZIAUTH_UPDATE_MSI"] = str(path)
    environment["YOUZIAUTH_UPDATE_EXE"] = str(executable)
    environment["PSModulePath"] = str(powershell.parent / "Modules")
    try:
        result = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-EncodedCommand", _ENCODED_COMMAND],
            env=environment, shell=False, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            encoding="utf-8", timeout=60, creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
    except subprocess.TimeoutExpired:
        raise UpdateVerificationError("更新安全验证超时，请稍后重试。") from None
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError):
        raise UpdateVerificationError(_VERIFICATION_FAILED) from None
    if result.returncode != 0:
        raise UpdateVerificationError(_ERRORS.get(result.returncode, _VERIFICATION_FAILED))
    try:
        return json.loads(result.stdout)
    except (ValueError, TypeError, RecursionError):
        raise UpdateVerificationError("更新安全验证返回了无效结果，已拒绝更新。") from None


def _expected_properties(version: str) -> dict[str, str]:
    return {
        "ProductName": "youziauth", "Manufacturer": "yoouzic",
        "ProductVersion": version, "UpgradeCode": _UPGRADE_CODE,
    }


def _verify_result(result: object, version: str) -> None:
    if not isinstance(result, dict):
        raise UpdateVerificationError("更新安全验证返回了无效结果，已拒绝更新。")
    exe = result.get("exe")
    if not isinstance(exe, dict) or exe.get("status") != "Valid":
        raise UpdateVerificationError(_OFFICIAL)
    subject = exe.get("subject")
    if not isinstance(subject, str) or not subject.strip():
        raise UpdateVerificationError(_OFFICIAL)
    msi = result.get("msi")
    if not isinstance(msi, dict) or msi.get("status") != "Valid":
        raise UpdateVerificationError(_ERRORS[11])
    if msi.get("timestamp") is not True:
        raise UpdateVerificationError(_ERRORS[12])
    if msi.get("subject") != subject:
        raise UpdateVerificationError(_ERRORS[13])
    properties = result.get("properties")
    expected = _expected_properties(version)
    if not isinstance(properties, dict) or any(properties.get(key) != value for key, value in expected.items()):
        raise UpdateVerificationError(_ERRORS[16])


def verify_msi(path: Path, executable: Path | None, version: str, sha256: str) -> None:
    """Installation must repeat this verification while holding the MSI file lock."""
    msi, anchor = _validate_inputs(path, executable, version, sha256)
    try:
        digest = hashlib.sha256()
        with msi.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        raise UpdateVerificationError("无法读取 MSI 安装包以校验哈希。") from None
    if not hmac.compare_digest(digest.hexdigest(), sha256.lower()):
        raise UpdateVerificationError("MSI 安装包 SHA256 哈希不匹配，已拒绝更新。")
    _verify_result(_powershell_result(msi, anchor), version)


def _start_worker(msi: Path, anchor: Path, version: str, sha256: str, directory: Path) -> int:
    powershell = _system_tool("WindowsPowerShell/v1.0/powershell.exe")
    request = {
        "directory": str(directory), "sha256": sha256.lower(),
        "properties": _expected_properties(version), "deadline": int(time.time()) + 90,
    }
    environment = os.environ.copy()
    environment.update({
        "YOUZIAUTH_UPDATE_MSI": str(msi), "YOUZIAUTH_UPDATE_EXE": str(anchor),
        "YOUZIAUTH_UPDATE_REQUEST": base64.b64encode(json.dumps(request).encode("utf-8")).decode("ascii"),
        "YOUZIAUTH_UPDATE_VERIFY": _ENCODED_COMMAND, "YOUZIAUTH_UPDATE_WORKER": _ENCODED_WORKER,
        "PSModulePath": str(powershell.parent / "Modules"),
    })
    try:
        result = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-EncodedCommand", _ENCODED_LAUNCHER],
            env=environment, shell=False, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            encoding="utf-8", timeout=20, creationflags=0x08000000,
        )
        status = json.loads(result.stdout)
        if (result.returncode != 0 or not isinstance(status, dict) or set(status) != {"pid"}
                or type(status["pid"]) is not int or not 0 < status["pid"] <= 0xFFFFFFFF):
            raise ValueError
        return status["pid"]
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, RecursionError):
        raise UpdateVerificationError(_ERRORS[18]) from None


def _read_status(path: Path) -> dict | None:
    try:
        with path.open("r", encoding="utf-8") as stream:
            data = stream.read(4097)
        if len(data) > 4096:
            raise ValueError
        result = json.loads(data)
        if not isinstance(result, dict):
            raise ValueError
        return result
    except FileNotFoundError:
        return None
    except (OSError, ValueError, RecursionError):
        raise UpdateVerificationError("更新辅助进程状态无效，请检查安装向导状态。") from None


def _observe_worker(pid: int, directory: Path, on_launch: Callable[[], None]) -> int:
    api = _kernel32()
    handle = None
    finished = False
    notified = False
    notification_error = None
    deadline = time.monotonic() + 120
    try:
        handle = api.OpenProcess(0x00100000, False, pid)
        exited = not handle
        while True:
            final = _read_status(directory / "final.json")
            launch = _read_status(directory / "launch.json")
            if launch is not None:
                if (set(launch) != {"pid"} or type(launch["pid"]) is not int
                        or not 0 < launch["pid"] <= 0xFFFFFFFF):
                    raise UpdateVerificationError(_ERRORS[20])
                if not notified:
                    notified = True
                    try:
                        on_launch()
                    except BaseException as exc:
                        notification_error = exc
            if final is not None:
                if (set(final) not in ({"code"}, {"error"}) or
                        type(next(iter(final.values()))) is not int):
                    raise UpdateVerificationError(_ERRORS[20])
                finished = True
                if "error" in final:
                    raise UpdateVerificationError(_ERRORS.get(final["error"], _VERIFICATION_FAILED))
                if not notified or not -0x80000000 <= final["code"] <= 0xFFFFFFFF:
                    raise UpdateVerificationError(_ERRORS[20])
                if notification_error is not None:
                    if not isinstance(notification_error, Exception):
                        raise notification_error
                    raise UpdateVerificationError("安装向导已启动，但启动通知失败；请检查安装结果。") from None
                return final["code"]
            if exited:
                finished = bool(handle)
                raise UpdateVerificationError("更新辅助进程意外退出或无法观察，请检查安装向导状态。")
            if not notified and time.monotonic() >= deadline:
                raise UpdateVerificationError(_ERRORS[18])
            wait = api.WaitForSingleObject(handle, 100)
            if wait not in (0, 258):
                raise UpdateVerificationError(_ERRORS[20])
            exited = wait == 0
    except (OSError, ValueError):
        raise UpdateVerificationError(_ERRORS[20]) from None
    finally:
        if handle:
            api.CloseHandle(handle)
        if finished:
            shutil.rmtree(directory, ignore_errors=True)


def install_msi(
    path: Path, executable: Path | None, version: str, sha256: str,
    on_launch: Callable[[], None],
) -> int:
    if not callable(on_launch):
        raise UpdateVerificationError("安装启动通知回调无效。")
    msi, anchor = _validate_inputs(path, executable, version, sha256)
    try:
        directory = Path(tempfile.mkdtemp(prefix="youziauth-update-")).resolve()
    except OSError:
        raise UpdateVerificationError("无法创建更新私有状态目录，已取消更新。") from None
    pid = _start_worker(msi, anchor, version, sha256, directory)
    return _observe_worker(pid, directory, on_launch)
