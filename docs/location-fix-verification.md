# Windows location fix — 1.3.1

## Cause and evidence

The user's Windows location service was running, and machine, user and desktop-app consent values were all `Allow`. A location-only reproduction of the old provider returned:

```json
{"started":true,"status":"NoData","permission":"Granted","unknown":true}
```

The old code treated `TryStart` success as a completed position fix, immediately inspected `Position.IsUnknown`, then collapsed all failures into a misleading permissions message. It also suppressed permission prompting.

An independent Windows WinRT location-only probe on the same computer returned a current Wi-Fi position with approximately 141 metres horizontal accuracy. No coordinates were printed or saved in the diagnostic report, and no school API was called by that probe.

## Changes

- `dorm_location.py` now waits for `Geolocator.get_geoposition_async_with_age_and_timeout`, with a bounded 30-second provider timeout. Existing 200-metre accuracy / 120-second age checks remain; user default, IP and obfuscated locations are rejected.
- Permission denied, service disabled, no provider, no data, timeout, inaccurate, stale and invalid data have distinct controlled messages.
- The desktop button **授权并检测定位** requests `Geolocator.request_access_async` on the foreground WinForms UI thread, then waits and probes off the UI thread. It never edits consent registry keys or bypasses a Windows choice. Denied permissions still require user action in Windows settings.
- Location diagnostics have separate busy/status state and never invoke `DormController.start`, school login or attendance submission. The frontend receives only status, accuracy and check time, never coordinates.
- The actual attendance engine uses the same corrected provider and obtains a new location for each submission.
- All PyWinRT modules and their MIT notice are bundled. MSI version is 1.3.1.

## Validation

- 205 Python tests passed, including no-data versus denial, quality checks, local-only diagnostics, simulated permission denial, no secret/coordinate exposure and controller isolation.
- 4 existing frontend regression tests passed; Python compilation and `git diff --check` passed.
- The source desktop's real authorization button completed successfully on this computer, displaying approximately 141 metres accuracy. Evidence: `build/qa/location/source-authorize-success.png`.
- The packaged EXE starts and displays the new location button. The final location-only packaged self-test result is recorded in `build/qa/location/packaged-probe.json`; the package hash manifest is alongside it.
- The system had already allowed desktop-app location, so a first-use allow/deny popup was not visually exercised; denial behavior is covered with mocks.
- Real school submission was not invoked by the diagnostic checks. Existing user-enabled automatic tasks in the installed app are independent of this diagnostic run.

## Reproduction commands

```powershell
.tools/desktop-python/Scripts/python.exe -m unittest discover -s tests
node --test tests/test_desktop_ui.cjs
.tools/desktop-python/Scripts/python.exe campus_auth_desktop.py --location-diagnostic
dist/youziauth/youziauth.exe --location-self-test build/qa/location/packaged-probe.json
```

`--location-diagnostic` uses real Windows location only when its authorization button is clicked; all school/network actions remain synthetic. Ordinary `--preview` never reads real location. `--location-self-test` performs a local position probe using existing permission and writes only the quality summary, without opening the normal controller.

Microsoft API guidance: [foreground UI-thread permission request](https://learn.microsoft.com/en-us/uwp/api/windows.devices.geolocation.geolocator.requestaccessasync), [waiting for a geoposition](https://learn.microsoft.com/en-us/uwp/api/windows.devices.geolocation.geolocator.getgeopositionasync).
