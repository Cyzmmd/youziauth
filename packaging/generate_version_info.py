from __future__ import annotations

import argparse
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_version() -> tuple[str, tuple[int, int, int, int]]:
    value = ROOT.joinpath("VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value):
        raise ValueError("VERSION must use MAJOR.MINOR.PATCH")
    major, minor, patch = (int(part) for part in value.split("."))
    return value, (major, minor, patch, 0)


def render(
    version: str,
    numbers: tuple[int, int, int, int],
    description: str,
    filename: str,
) -> str:
    numeric = ", ".join(str(part) for part in numbers)
    return f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({numeric}), prodvers=({numeric}), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('080404b0', [
    StringStruct('CompanyName', 'yoouzic'),
    StringStruct('FileDescription', '{description}'),
    StringStruct('FileVersion', '{version}'),
    StringStruct('InternalName', '{filename.removesuffix(".exe")}'),
    StringStruct('LegalCopyright', 'Copyright (C) 2026 yoouzic'),
    StringStruct('OriginalFilename', '{filename}'),
    StringStruct('ProductName', 'youziauth'),
    StringStruct('ProductVersion', '{version}')
  ])]), VarFileInfo([VarStruct('Translation', [2052, 1200])])]
)
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    version, numbers = load_version()
    args.output.mkdir(parents=True, exist_ok=True)
    values = {
        "youziauth.version": (
            "youziauth campus network tray and settings",
            "youziauth.exe",
        ),
        "youziauth-agent.version": (
            "youziauth SYSTEM campus network authentication agent",
            "youziauth-agent.exe",
        ),
    }
    for name, (description, filename) in values.items():
        args.output.joinpath(name).write_text(
            render(version, numbers, description, filename), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
