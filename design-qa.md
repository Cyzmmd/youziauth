# Unified desktop visual QA — 2026-09-21

final result: passed

Scope: the approved paper/ink visual adaptation and synthetic UI interactions, not real campus authentication, real attendance, full accessibility certification or an installed-MSI upgrade.

## Reference and evidence

- User supplied three SWU TIC theme screenshots. Main visual reference: `codex-clipboard-29c7c641-2082-41e7-a150-9c94195159dc.png`, 3072 × 1676 pixels. This is a style adaptation for a desktop utility, not a pixel-identical clone of the teaching management product.
- Inspected the reference and current saved overview together. Reused the exact local cotton-paper texture and palette from that project's CSS. Kept this project's yuzu mark and actual network/dorm workflows.
- Native WebView2 screenshots: `build/qa/desktop/01-overview.png`, `02-network.png`, `03-dorm.png`, `04-records.png`. The first three were captured from the source preview; the records screenshot from the final packaged EXE. Maximized captures are 1536 × 960, including native title bar. Desktop screenshots are logical-pixel captures; they are not claimed to have the same device scale as the user's reference.
- Default window: 1160 × 820 (bounded to screen size). Browser layout checks at 1160 × 820, minimum desktop 820 × 620 and extra narrow 560 × 800 found no horizontal document overflow. At 560px the sidebar collapses to 76px; this is additional resilience, not a mobile-app delivery.
- The in-app browser's full-page screenshot scaling was unreliable, so native-window screenshots are the accepted visual evidence. Initial occluded screenshots were rejected.

## Page checks

1. **今日概览 — passed.** Shared navigation, paper texture, current connection and dorm state, background arrangements and useful links. Native executable's simulated network check updates its status card successfully.
2. **校园网 — passed.** Saved-settings form, visible password toggle, interval, startup option and monitoring controls. Blank password preserves the existing secret in backend tests. Draft edits survive polling and failed saves; stopping remains possible with an unsaved draft.
3. **寝室打卡 — passed.** It is a page in the same window. Query changes the empty task to a synthetic task. Submit confirmation and cancellation work; confirmed synthetic submission changes status to completed and disables another submit. Windows-location and school-login operations are implemented but were not invoked against real services.
4. **运行记录 — passed.** Network and dorm tabs display the corresponding records; empty state and refresh work. Synthetic browser flows produced both query and completion records. Native packaged EXE produced its simulated network-check record.

## Fidelity surfaces

- **Typography:** body uses Segoe UI / Microsoft YaHei, with the reference's serif treatment reserved for the overview display heading. Functional headings and labels remain sans serif. Small notes are subordinate; at default size longer pages scroll normally.
- **Spacing and hierarchy:** fixed left navigation, framed top bar, 18px card radii, consistent spacing and form alignment. The original large dark log pane and separate dorm window are removed from the default experience. Overview layout deliberately combines the reference's editorial display type and workspace cards.
- **Colour:** paper `#f4f2ec`, ink `#465a70`, text `#29333e`, borders `#dcddd9`, restrained gold. Status badges preserve distinct green, ochre and brick semantics. Controls have visible focus outlines; disabled actions remain visually subordinate.
- **Assets:** original cotton-paper WebP and yuzu PNG; local Bootstrap icon fonts. All resources packaged locally. No placeholder illustrations, CDN dependency or invented branding.
- **Copy:** rewritten for campus connectivity and dorm tasks. Separate credentials and location disclosure remain explicit. Demo state is visibly marked and cannot access real services.

## Findings fixed

- P2: dirty network settings previously blocked Stop as well as Check/Start. Stop now bypasses the draft guard.
- P2: saving could overwrite an edit entered while awaiting a save/UAC operation. Form revision checks now preserve later edits; snapshot generations reject pre-mutation responses.
- P2: launching a GUI executable with the launcher’s hidden-window flag could leave it invisible. The launcher now requests a normal window, while hidden startup remains an explicit application mode.
- P2: Windows could associate source launches with Python's taskbar identity. The process now sets the `youziauth` AppUserModelID before creating windows; both MSI shortcuts explicitly specify the yuzu icon and matching identity. Window title icon is visually verified. Existing installed/pinned shortcuts were not changed in this run.

## Verification and limits

- 196 Python tests passed; 4 Node frontend regression tests passed.
- Python compilation and `git diff --check` passed.
- Independent code review's two actionable findings were fixed and covered by frontend regression tests.
- PyInstaller EXE and WiX MSI 1.3.0 built successfully. Final packaged EXE launched and rendered the new UI; its JS-to-Python preview bridge executed a simulated network check and displayed the resulting record.
- Close-to-tray, no-tray minimization, same-window dorm navigation, explicit exit cleanup and taskbar identity have mocked lifecycle tests. A complete manual tray round trip and installed-MSI upgrade were not performed.
- Native school login, real location, real submissions, production startup registration, real credential migration and end-to-end system-agent operation were not performed. Existing engine tests remain passing; this is not a claim of a successful real attendance submission.
- P3 follow-up: user preference tuning of small explanatory text sizes after use on their usual display.

## Delivery

`dist/youziauth.msi` is the new installer. `start_desktop_preview.vbs` opens an isolated desktop demo (prefers the packaged EXE). `start_gui_silent.vbs` starts the actual app, using the local desktop environment when available. The legacy installed app and its running windows are left intact.

## 1.3.1 location addendum

Added the authorization/quality-check panel in the dorm location card. Real source-window permission + position check passed at 141m; final packaged EXE position-only self-test passed with exit 0 at 141m. Details and evidence scope: `docs/location-fix-verification.md`. 205 Python and 4 frontend tests passed for this revision. Installation of 1.3.1 is still a user step; the currently installed 1.3.0 app was not overwritten by these tests.
