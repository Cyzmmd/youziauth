# MSI payload completeness fix — 1.4.1

## Symptom

The 1.4.0 MSI installed "successfully" (`Installation success or error status: 0`), but the
installed application could not start:

```
Error
Failed to start embedded python interpreter: Failed to import encodings module
```

## Cause and evidence

The frozen interpreter starts from `_internal\base_library.zip`. That file was present in
the built bundle and inside the MSI payload, but it never reached
`C:\Program Files (x86)\youziauth\_internal\`.

The 1.4.0 upgrade ran while the previously installed tray application and its elevated
agent were still running. Both processes hold `python314.dll`, `VCRUNTIME140.dll` and
`base_library.zip` open, so Windows Installer added those file copies to the pending
reboot queue instead of writing them. A user who launches the application before that
reboot gets an interpreter with no standard library.

Evidence collected on the affected machine:

| Check | Result |
| --- | --- |
| Files expected from the bundle | 1711 |
| Files present after the 1.4.0 upgrade | 1667 |
| Difference | 41 files: `_internal\base_library.zip`, `_internal\ucrtbase.dll` and 39 `api-ms-win-*-l1-1-0.dll` stubs |
| MSI administrative extraction of the same package | contained `_internal\base_library.zip` (so the package itself was complete) |
| `msiexec /f {ProductCode}` plus reboot | restored all 1708 files, application started normally |
| Installer log | `RESTART MANAGER: Did detect that a critical application holds file[s] in use, so a reboot will be necessary.` |
| In-use report | `The file ...\_internal\python314.dll is being used by the following process: Name: youziauth-agent` |

The tray application and the SYSTEM agent are registered as scheduled tasks
(`\youziauth\Tray`, `\youziauth\SystemAgent`), so they are running whenever an upgrade
starts. Windows Installer cannot prompt them to close because the PyInstaller windowed
build exposes no Restart Manager callback, and the agent runs elevated in another session.

Version 1.4.0 was also built before this defect was understood, and `dist\youziauth.msi`
was the 1.3.1 build, so a rebuilt package could be mistaken for "the latest installer".

## Changes

- `packaging/youziauth.wxs` runs a `StopYouziauthProcesses` custom action
  (`taskkill /f /t /im youziauth-agent.exe` and `youziauth.exe`, twice with a one-second
  settle in between) `Before="InstallValidate"` and only when `NOT REMOVE`, so no payload
  file is open while files are written and no reboot is required. The action runs
  impersonated as SYSTEM, so the elevated agent is terminated too.
- `packaging/youziauth.wxs` adds a launch condition for `VersionNT >= 603`, matching the
  WebView2 requirement.
- `packaging/verify_msi_payload.ps1` extracts the built MSI with `msiexec /a` and compares
  every file (name and size) against the PyInstaller bundle, failing on missing, differing
  or unexpected payload files.
- `build_msi.ps1` always checks that the generated `ApplicationFiles.wxs` covers every
  bundle file, and runs the payload comparison when `-VerifyPayload` is passed.
- `.github/workflows/ci.yml` and `.github/workflows/release.yml` build with
  `-VerifyPayload`, so a package that does not carry the whole bundle cannot be uploaded
  or signed.
- `VERSION` is `1.4.1` for the payload fix and `1.4.2` for the location-source build. The
  installed 1.4.0 is a *lower* version, so Windows Installer accepts these builds as a major
  upgrade instead of refusing them. See `docs/location-source-selector.md` for 1.4.2.

## Validation

- 266 Python tests pass, including new payload invariants: the WiX source must stop both
  processes before `InstallValidate`, the build script must verify the manifest and support
  `-VerifyPayload`, the verifier must compare every bundle file, and the release workflow
  must verify the payload before it submits the artifact for signing.
- `build_msi.ps1 -VerifyPayload` passes on this machine and reports
  `MSI payload verified: 1708 files match dist\youziauth`; the generated manifest reports
  `WiX manifest covers all 1708 bundle files`.
- The rebuilt 1.4.1 MSI was installed on the machine that had hit the defect, after the
  previous product was uninstalled:
  - `msiexec /i dist\youziauth.msi` succeeded, the product is registered as `youziauth 1.4.1`.
  - Every one of the 1708 bundle files is present with a matching size; `_internal\base_library.zip`,
    `_internal\ucrtbase.dll` and the 39 `api-ms-win-*-l1-1-0.dll` stubs are all installed.
  - The install log contains `Doing action: StopYouziauthProcesses` and **no**
    `RESTART MANAGER: Did detect that a critical application holds file[s] in use` line and no
    restart event; the install ran to `MainEngineThread is returning 0` without a reboot.
  - Re-running the installer while the tray application was running (the exact 1.4.0 failure
    scenario) terminated the process, rewrote all 1708 files, and again required no reboot.
  - The installed `youziauth.exe` starts, stays responsive and writes no
    `Failed to start embedded python interpreter` error.

An `msiexec` run from a non-elevated shell fails with 1925/1730 for this per-machine
package — that is a caller privilege requirement of Windows Installer, not a packaging
defect. Install from an elevated prompt or accept the UAC prompt.

### Environment note

An upgrade also stops the SYSTEM agent, which is a scheduled task (`\youziauth\SystemAgent`)
and does not restart itself until the next logon. Re-enable 开机自启动 in the application to
recreate both tasks. The MSI does not manage these tasks because the application creates
them; an uninstall therefore leaves them behind pointing at a removed executable.

## Reproduction commands

```powershell
# Full package build with payload verification
.\build_msi.ps1 -PythonPath .\.tools\desktop-python\Scripts\python.exe -VerifyPayload

# Verify an existing package against an existing bundle
.\packaging\verify_msi_payload.ps1 -MsiPath dist\youziauth.msi -AppDir dist\youziauth

# Install or repair (needs an elevated prompt)
msiexec /i dist\youziauth.msi /L*v install.log
```

## Recovering an affected 1.4.0 installation

Installing the 1.4.1 package is enough: it stops the running processes and writes the
payload during the same transaction. For a machine that must stay on 1.4.0, close the
tray application (and the elevated agent), then run the repair command above; the
administrative extraction of the 1.4.0 package can also be copied over
`C:\Program Files (x86)\youziauth\_internal` manually.
