# Third-Party Notices

This document describes third-party software used to build or distributed with the Windows MSI release of youziauth. The youziauth source code remains licensed under GPL-3.0-only as stated in `LICENSE`.

## Components distributed in the Windows package

### CPython 3.14.0 and Windows runtime dependencies

The packaged executables include the CPython 3.14.0 interpreter and selected Python standard-library modules. The Windows Python distribution also incorporates or links runtime components whose notices are reproduced in the official CPython Windows license file, including Microsoft Distributable Code, bzip2/libbzip2, libffi, Zstandard, Apache-2.0-licensed components, and Tcl/Tk.

The complete license text supplied with the Python distribution used for this build is included at:

`third_party_licenses/CPYTHON-3.14-LICENSE.txt`

### Tcl/Tk 8.6.15

The legacy desktop interface uses Tkinter and the packaged application includes Tcl/Tk 8.6.15 runtime files. The Tcl/Tk terms are reproduced in the CPython license file above. PyInstaller also preserves Tcl/Tk's `license.terms` inside the packaged `_tk_data` directory.

### Unified desktop interface

The default interface uses pywebview 6.2.1 (BSD-3-Clause), Python.NET 3.1.0 (MIT), clr-loader 0.3.1 (MIT), Bottle 0.13.4 (MIT) and proxy_tools 0.1.0 (BSD text supplied by upstream; package metadata says MIT). Corresponding license files are distributed under `third_party_licenses`. pywebview's Windows bridge includes the Microsoft WebView2 SDK 1.0.3856.49 managed assemblies and loader; the SDK license and notice are included as `WEBVIEW2-SDK-LICENSE.txt` and `WEBVIEW2-SDK-NOTICE.txt`. The separately installed Microsoft Edge WebView2 Runtime is required and is not bundled in this MSI.

The UI uses locally bundled Bootstrap Icons (MIT; `BOOTSTRAP-ICONS-LICENSE.txt`). The paper texture is reused from the user's SWU TIC theme source, `app/static/images/textures/cotton-paper.webp`; the yuzu mark comes from this project's existing generated brand asset. No third-party web fonts or CDN resources are fetched at runtime.

Location uses PyWinRT 3.2.1 runtime and the Windows.Foundation / Windows.Devices.Geolocation projections (MIT). The upstream license is included as `PYWINRT-3.2.1-LICENSE.txt`. Location access is requested by the desktop application through Windows; no registry-based permission override or third-party geolocation service is used.

### OpenSSL 3.0.18

The packaged Python SSL module includes OpenSSL 3.0.18 libraries. OpenSSL 3.x is distributed under the Apache License 2.0; that license text is reproduced in the CPython Windows license file above.

### PyInstaller 6.16.0 bootloader

The Windows executables are produced with PyInstaller 6.16.0 and include its bootloader. PyInstaller is distributed under GPL version 2 or later with a special exception that permits distributing programs built with PyInstaller. The complete PyInstaller license and exception text is included at:

`third_party_licenses/PYINSTALLER-6.16-COPYING.txt`

## Build-time components not bundled as application runtime libraries

### Dormitory login runtime additions (integration preview)

Playwright 1.59.0 is used only when the user opens the interactive login flow. Its Python runtime and Node-based driver are bundled; Edge/Chrome itself is not bundled. Corresponding upstream texts are in `third_party_licenses/PLAYWRIGHT-1.59-LICENSE.txt`, `PLAYWRIGHT-NOTICE.txt`, `PLAYWRIGHT-NODE-LICENSE.txt`, and `PLAYWRIGHT-THIRD-PARTY-NOTICES.txt`.

The driver uses Apache-2.0-licensed Playwright and the separately licensed Node runtime and dependencies identified in those notices. Python dependencies greenlet 3.2.4 and pyee 13.0.1 have their upstream license texts in the same directory. These are distributed runtime additions, not just build tools.

The SWU adapter was implemented in this repository with the endpoint/field flow in `dan-cun/swu-daka` commit `42d8973` as a protocol reference (https://github.com/dan-cun/swu-daka). No upstream GUI, OCR code, fixed location defaults, user credentials or token cache is bundled. The original repository license is not changed by this integration.

### Pillow 12.2.0

Pillow is used by `packaging/make_icons.py` to generate icon files before packaging. The application does not import Pillow at runtime. Pillow 12.2.0 uses the MIT-CMU license; its license text is included for build reproducibility at:

`third_party_licenses/PILLOW-12.2-LICENSE.txt`

### WiX Toolset 7.0.0

WiX Toolset is invoked by `build_msi.ps1` to construct the MSI database. WiX is a build tool and is not installed as an application runtime component by the youziauth MSI. Its own package retains its upstream licensing information.

## Scope

The version numbers above describe the toolchain used for the corresponding release build. Rebuilders who use different Python or build-tool versions must review the licenses supplied with those versions and update this document when the distributed runtime contents change.
