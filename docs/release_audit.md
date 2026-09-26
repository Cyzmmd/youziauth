# youziauth Release Audit

## 2026-09-26 Pinned-Key Update Evidence

The release trust model changed: updates are now authenticated by an Ed25519 key
compiled into the client instead of an Authenticode certificate. SignPath was not
approved, and a certificate is no longer on the critical path. See
[release-signing.md](release-signing.md) and [signed-release-blockers.md](signed-release-blockers.md).

- Automated suite: 566 tests passed, 1 skipped (`python -m unittest discover -s tests`).
- `ed25519.py` matches all RFC 8032 section 7.1 vectors for key derivation, signing,
  and verification, and rejects a tampered message, a tampered signature, a
  mismatched key, the malleable `S + L` encoding, and non-canonical points.
- Signing the real `dist/youziauth.msi` (71,436,084 bytes) produced `SHA256SUMS.txt`,
  `youziauth.msi.ed25519`, and `release-provenance.json`.
  SHA-256 `b8ce52bb06cebcaf167bc93c8c54005856d4367b163e10c5cb1c7ef1ccd46d12`.
- The published signature verifies with the public key alone; a tampered signature,
  a wrong version, and a modified installer are all rejected.
- `windows_update.verifier_main` accepted the genuinely signed MSI (exit 0) and
  rejected: tampered signature, wrong version in the signed payload, another key's
  valid signature, a downgrade to 1.6.6, and a byte-modified installer.
- The real PowerShell worker ran against the signed MSI with only the installer
  target redirected to a no-op. It acquired the exclusive file lock, re-computed the
  digest, read the four MSI product properties, verified the Ed25519 signature, and
  published `launch.json` before handing off, then reported cancel code 1602.
  Nothing was installed.
- The installed build and the built MSI are both 1.6.6 and still unsigned
  (`Get-AuthenticodeSignature` → `NotSigned`), so the first pinned-key release must
  be installed manually once.
- Not yet done: publishing a GitHub Release with the four assets, and one full
  in-app auto-update between two pinned-key versions.

## Cross-PC Runtime Checks

- The desktop installer must not ship local `config.ini`, `campus_auth_password.txt`, or `campus_auth.log`.
- The installed app writes settings to `%ProgramData%\youziauth\config.ini` and a machine-scope DPAPI blob to `credential.dat`.
- On first launch, the app creates that config from bundled `config.example.ini` or migrates a legacy AppData config.
- Relative log paths resolve beside the user config, not inside Program Files.
- The tray app uses Windows `Shell_NotifyIcon` directly and falls back to minimizing if the tray cannot be created.
- The packaged executable is expected to include Python, Tk, Tcl/Tk runtime files, and tray status icons through PyInstaller.

## MSI Build Flow

1. Run `powershell -ExecutionPolicy Bypass -File .\build_msi.ps1 -InstallDependencies`.
2. The script generates status icons.
3. PyInstaller builds `dist\youziauth\youziauth.exe` and `youziauth-agent.exe`.
4. WiX builds `dist\youziauth.msi`.
5. Install the MSI on a clean Windows machine and launch `youziauth` from the Start Menu.

If Python is not on PATH, pass `-PythonPath "C:\Path\To\python.exe"`. The build
machine must have a working Tkinter/Tcl installation; restricted sandboxes can
make Tkinter look broken even when the same Python works normally.

## UI Optimization Notes

- The desktop app now uses a visible status dot that matches tray state.
- Tray icon resources are color-coded: green online, red offline, amber checking, gray stopped, dark red error.
- The interface remains compact and operational: account, password, interval, log, status, and tray behavior are all visible without decorative sections.
- More animation is intentionally avoided for a background utility; status transitions should be fast and non-distracting.

## Manual Acceptance Checklist

- Install MSI on a Windows machine without project files.
- Launch from Start Menu.
- Confirm `%APPDATA%\youziauth\config.ini` is created.
- Fill account, password, and interval; save settings.
- Start background check.
- Close the window; confirm the tray icon remains.
- Confirm the tray tooltip changes after check results.
- Right-click tray icon and verify Show, Settings, Check Now, and Exit.
- Uninstall from Windows Apps and Features.

## 2026-07-19 Public Release Evidence

- Release version: `1.1.3`.
- Automated tests: 123 passed with zero failures (`python -m unittest discover -s tests`).
- Python source and test files compiled successfully with `python -m py_compile`.
- PyInstaller: 6.16.0; Python: 3.14.0; WiX 7 build pipeline completed successfully.
- MSI: `dist\youziauth.msi`, 15,402,008 bytes.
- MSI SHA-256: `E37C39DDB9A54E62FE6C72B00E1511F565A73BCB99DB6AE4A3AE124D83882213`.
- WiX MSI validation: exit code 0.
- Packaged file count: 1,008.
- The package includes `THIRD_PARTY_NOTICES.md` plus the CPython 3.14, PyInstaller 6.16, and Pillow 12.2 license files.
- Pillow is build-only; no Pillow runtime package files are present in the application bundle.
- Packaged tree contains zero `config.ini`, `credential.dat`, `campus_auth_password.txt`, or `campus_auth.log` files.
- Binary scans found no copy of the local password, password-file value, login URL, Windows user name, user-profile path, or Git commit email.
- Git tracks none of the sensitive runtime files above, and the release commit contains no local password or login URL value.

## 2026-07-19 Defender Incident Evidence

- Microsoft Defender recorded `Trojan:Win32/Wacatac.C!ml` at `2026-07-19 13:47:26` and quarantined the installed `youziauth-agent.exe` together with the `\youziauth\SystemAgent` task.
- The final agent runtime/log update before quarantine was `2026-07-19 13:47:17`, which explains both the stopped authentication checks and the frozen log view.
- The installed agent and the locally built release agent matched at SHA-256 `F13D60574D1102494641C510C26FCBFBD5B762E2BB5188911A2BD3E86C4B0370`.
- The affected 1.1.3 binary was unsigned. This is a reputation and provenance weakness, but is not by itself proof that the detection was correct or the only reason for it.
- No broad Defender exclusion was added.
- Recovery on `2026-07-20` re-registered the scheduled tasks through the elevated helper and ran the hidden SYSTEM task. One Agent process appeared, user-to-SYSTEM named-pipe status returned `ok: true / online_campus`, and the runtime snapshot reported `already authenticated` at `23:43:19+08:00`.
- The log advanced from `23:43:49` to `23:44:22` over one configured check cycle, and the runtime snapshot advanced with it. The latest Defender record remained the original `2026-07-19 13:47` incident; no new youziauth detection appeared during this recovery check.
- A post-fix reboot acceptance test has not yet been repeated.

## 2026-07-20 Trusted Release Hardening Evidence

- Local automated suite: 145 tests passed with zero failures after adding agent-health, immediate-repair, and release-policy coverage.
- The local 1.1.4 pre-signing build completed with pinned Python 3.14.0, PyInstaller 6.16.0, Pillow 12.2.0, and WiX 7.0.0 inputs.
- `youziauth.exe` and `youziauth-agent.exe` both report FileVersion and ProductVersion `1.1.4`; their file descriptions are distinct, and UPX is disabled.
- The final locally rebuilt unsigned MSI is 15,410,208 bytes with SHA-256 `95C9756B5AB76268D765A0265C1BA0E3E7DEDD0505532520F535FBC385FB27B0`. WiX validation passed, while the release verifier rejected it with `MSI signature is NotSigned`, as required; it is not eligible for public release.
- SignPath Foundation approval, the first signed workflow run, Microsoft submission verdicts, and clean-machine acceptance have not yet occurred and are not claimed here.

## 2026-07-19 System-Boot Pre-Release Evidence

- Automated tests: 122 passed with zero failures (`python -m unittest discover -s tests -v`).
- PyInstaller: 6.16.0; WiX: 7 build pipeline completed successfully.
- MSI: `dist\youziauth.msi`, 15,385,552 bytes.
- MSI SHA-256: `CB33D407E79EFC7D2CE5A5DB44C59CADE3870FD68B38497D30BA26F9EFA55DF3`.
- WiX MSI validation: exit code 0.
- Packaged file count: 1,004.
- MSI File table contains both `youziauth.exe` and `youziauth-agent.exe`; ProductVersion is `1.1.3`.
- MSI Registry table contains the `URL Protocol` marker and the command `youziauth.exe --notification-action "%1"`.
- Packaged tree contains zero `config.ini`, `credential.dat`, `campus_auth_password.txt`, or `campus_auth.log` files.
- Packaged agent smoke test exited 2 with the example configuration, as expected for missing real credentials; it opened no console window.
- Task XML is covered by tests for a zero-delay BootTrigger, SYSTEM principal, priority 4, interactive LogonTrigger, and hidden `--tray-startup` mode.
- Live task registration passed on 1.1.3: the SYSTEM task exported with BootTrigger, SID `S-1-5-18`, priority 4, `PT0S`, and the installed agent command; the tray task exported with InteractiveToken and `--tray-startup`.
- Manual SYSTEM-task run created the agent and produced `online_campus / already authenticated`; the first verified snapshot followed process creation by about one second.
- Tray-task run created one hidden `--tray-startup` process. Settings protocol opened the existing window, then close returned it to hidden state.
- User-to-SYSTEM named-pipe status, retry, and current-boot suppression commands passed after the task supplied the configured user SID to the pipe ACL.
- A real three-action toast was accepted by Windows. The packaged and live logs contained no password/query-string tokens.
- Reboot acceptance on 2026-07-19 passed: boot `11:14:15`, SYSTEM agent `11:14:33`, Explorer `11:14:45`, hidden tray `11:14:53`, first authenticated status log `11:14:58`.
- Measured timing: boot-to-agent 18.1 s, agent 11.8 s before Explorer, boot-to-first-status 42.5 s, and Explorer-to-tray 8.0 s. The prior Startup-folder baseline was boot-to-app 81.7 s and Explorer-to-app 59.3 s.
- After reboot the tray had no main window handle, ran with `--tray-startup`, user-to-SYSTEM IPC returned `online_campus`, and current-boot notification suppression reset to false under the new boot ID.
- The portal reported `already authenticated`, so this reboot validates early status detection rather than a forced credential POST. Hotspot behavior and true rejection remain covered by deterministic tests; they were not forced on the live network.

## 2026-05-25 Verification

- Built `dist\youziauth.msi` with WiX 7 and PyInstaller 6.20.0.
- MSI size: 10,195,540 bytes.
- Ran `python -m unittest discover -s tests`: 46 tests passed.
- Ran `python -m py_compile` for app and packaging scripts.
- Confirmed the PyInstaller output includes `_tkinter.pyd`, Tcl/Tk DLLs, and `_tcl_data`/`_tk_data`.
- Confirmed `dist\youziauth` does not contain `config.ini`, `campus_auth_password.txt`, or `campus_auth.log`.
- Launched `dist\youziauth\youziauth.exe` for a 3-second smoke test; the process stayed running.
- `wix msi validate` could not complete because the current environment cannot access the Windows Installer service.
