# Third-Party Notices

This document describes third-party software used to build or distributed with the Windows MSI release of youziauth. The youziauth source code remains licensed under GPL-3.0-only as stated in `LICENSE`.

## Components distributed in the Windows package

### CPython 3.14.0 and Windows runtime dependencies

The packaged executables include the CPython 3.14.0 interpreter and selected Python standard-library modules. The Windows Python distribution also incorporates or links runtime components whose notices are reproduced in the official CPython Windows license file, including Microsoft Distributable Code, bzip2/libbzip2, libffi, Zstandard, Apache-2.0-licensed components, and Tcl/Tk.

The complete license text supplied with the Python distribution used for this build is included at:

`third_party_licenses/CPYTHON-3.14-LICENSE.txt`

### Tcl/Tk 8.6.15

The desktop interface uses Tkinter and the packaged application includes Tcl/Tk 8.6.15 runtime files. The Tcl/Tk terms are reproduced in the CPython license file above. PyInstaller also preserves Tcl/Tk's `license.terms` inside the packaged `_tk_data` directory.

### OpenSSL 3.0.18

The packaged Python SSL module includes OpenSSL 3.0.18 libraries. OpenSSL 3.x is distributed under the Apache License 2.0; that license text is reproduced in the CPython Windows license file above.

### PyInstaller 6.16.0 bootloader

The Windows executables are produced with PyInstaller 6.16.0 and include its bootloader. PyInstaller is distributed under GPL version 2 or later with a special exception that permits distributing programs built with PyInstaller. The complete PyInstaller license and exception text is included at:

`third_party_licenses/PYINSTALLER-6.16-COPYING.txt`

## Build-time components not bundled as application runtime libraries

### Pillow 12.2.0

Pillow is used by `packaging/make_icons.py` to generate icon files before packaging. The application does not import Pillow at runtime. Pillow 12.2.0 uses the MIT-CMU license; its license text is included for build reproducibility at:

`third_party_licenses/PILLOW-12.2-LICENSE.txt`

### WiX Toolset 7.0.0

WiX Toolset is invoked by `build_msi.ps1` to construct the MSI database. WiX is a build tool and is not installed as an application runtime component by the youziauth MSI. Its own package retains its upstream licensing information.

## Scope

The version numbers above describe the toolchain used for the corresponding release build. Rebuilders who use different Python or build-tool versions must review the licenses supplied with those versions and update this document when the distributed runtime contents change.
