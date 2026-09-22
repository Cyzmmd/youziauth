# 开机自启动失败诊断与修复 — 1.4.2

## Symptom

Toggling 开机自启动 and saving showed the generic bridge failure text:

```
操作未完成，请检查本机权限、配置和网络后重试。
```

## Cause and evidence

`startup_tasks.relaunch_elevated_configuration` used `ShellExecuteW("runas", ...)`. That call
only reports that the UAC dialog was *accepted* — it never reports what the elevated helper
did. `campus_auth_gui.set_startup_enabled` then re-read `is_system_startup_enabled()`, saw the
state unchanged, and reported 开机自启动未变更, which the bridge rendered as the generic
message. A declined or failed elevation was therefore indistinguishable from a silent no-op.

Reproduced on this machine by calling the bridge with the real configuration:

| Step | Observed |
| --- | --- |
| `gui.set_startup_enabled(False)` through the old path | `ShellExecuteW` returned success, tasks unchanged |
| `bridge.dispatch('network_save', {... 'startup': True})` with `_agent` already true | generic failure message |
| Direct `schtasks /Create` without elevation | `ERROR: Access is denied.` behind the same generic message |
| Elevated `youziauth.exe --configure-system-startup=enable` run by hand | exit 0, both tasks created, agent started |

The elevated helper itself was correct: its exit code carries the real outcome
(`_run_elevated_startup_configuration` returns 1 on failure, 2 on an invalid mode). The bug was
that nobody read it.

## Changes

- `startup_tasks.launch_elevated` runs the helper with `ShellExecuteExW` plus
  `SEE_MASK_NOCLOSEPROCESS`, waits for the process (30 s cap) and returns its exit code.
  `ERROR_CANCELLED` (1223) becomes `PermissionError`, a wait timeout becomes `TimeoutError`.
- `campus_auth_gui.set_startup_enabled` now waits for that exit code and then verifies
  `is_system_startup_enabled()`, raising a specific `RuntimeError` for each outcome:
  a non-zero helper exit code, a declined UAC prompt, or a state that still does not match.
- `CampusAuthGui.repair_system_agent` uses the same observability and reports the failure in
  the tray status.
- `desktop_ui/app.js` warns before a save that changes 开机自启动 that Windows will ask for
  administrator approval, because the bridge now blocks until that dialog is answered.
- `relaunch_elevated_configuration` is kept for compatibility but no longer used by the UI.

## Validation

- 280 Python tests pass, including five new `launch_elevated` tests (runas verb, exit-code
  propagation, ERROR_CANCELLED, unexpected shell error, timeout) and four new
  `set_startup_enabled` tests (waits and verifies, non-zero exit, declined UAC, unverified
  state). `repair_system_agent` now has coverage for a declined prompt as well.
- 35 frontend tests pass, including the new administrator-approval notice for a startup change
  and the absence of that notice when startup is untouched.
- Live verification on this machine:
  - `launch_elevated(['--configure-system-startup=disable'|'enable'],
    youziauth.exe)` returned exit 0 for both and the detected state followed
    (`True → False → True`);
  - a `cmd.exe` probe through the same launcher returned the helper's real exit code (9) and
    confirmed the child runs at High integrity level (`S-1-16-12288`);
  - an unelevated `schtasks /Create` reproduces `ERROR: Access is denied.`, which the new code
    now reports as an explicit failure instead of "未变更".

## Reproduction

```powershell
# Elevated helper result, without the UI
.tools/desktop-python/Scripts/python.exe -c "import startup_tasks,pathlib; print(startup_tasks.launch_elevated(['--configure-system-startup=enable'], pathlib.Path(r'C:\Program Files (x86)\youziauth\youziauth.exe')))"

# Unit coverage
.tools/desktop-python/Scripts/python.exe -m unittest tests.test_startup_tasks tests.test_campus_auth_gui -v
node --test tests/test_desktop_ui.cjs
```
