from __future__ import annotations

import argparse
import html
import uuid
from pathlib import Path


WIX_NS = "http://wixtoolset.org/schemas/v4/wxs"
PRODUCT_NAMESPACE = uuid.UUID("79df1cda-236e-43a1-9981-a437dd93bce9")


def make_id(prefix: str, relative_path: Path) -> str:
    digest = uuid.uuid5(PRODUCT_NAMESPACE, relative_path.as_posix()).hex
    return f"{prefix}_{digest}"


def esc(value: Path | str) -> str:
    return html.escape(str(value), quote=True)


def write_directory(path: Path, app_dir: Path, indent: int, component_ids: list[str]) -> list[str]:
    lines: list[str] = []
    pad = " " * indent
    for file_path in sorted(path.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
        relative = file_path.relative_to(app_dir)
        if file_path.is_dir():
            directory_id = make_id("dir", relative)
            lines.append(f'{pad}<Directory Id="{directory_id}" Name="{esc(file_path.name)}">')
            lines.extend(write_directory(file_path, app_dir, indent + 2, component_ids))
            lines.append(f"{pad}</Directory>")
        elif file_path.is_file():
            component_id = make_id("cmp", relative)
            file_id = make_id("fil", relative)
            component_guid = str(uuid.uuid5(PRODUCT_NAMESPACE, f"component:{relative.as_posix()}")).upper()
            component_ids.append(component_id)
            lines.append(f'{pad}<Component Id="{component_id}" Guid="{{{component_guid}}}">')
            lines.append(
                f'{pad}  <File Id="{file_id}" Source="{esc(file_path)}" KeyPath="yes" />'
            )
            lines.append(f"{pad}</Component>")
    return lines


def generate(app_dir: Path, output: Path) -> None:
    component_ids: list[str] = []
    lines = [
        f'<Wix xmlns="{WIX_NS}">',
        "  <Fragment>",
        '    <DirectoryRef Id="INSTALLFOLDER">',
    ]
    lines.extend(write_directory(app_dir, app_dir, 6, component_ids))
    lines.extend(
        [
            "    </DirectoryRef>",
            "  </Fragment>",
            "  <Fragment>",
            '    <ComponentGroup Id="ApplicationFiles">',
        ]
    )
    for component_id in component_ids:
        lines.append(f'      <ComponentRef Id="{component_id}" />')
    lines.extend(
        [
            "    </ComponentGroup>",
            "  </Fragment>",
            "</Wix>",
            "",
        ]
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate WiX file entries for app files.")
    parser.add_argument("--app-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    generate(args.app_dir.resolve(), args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
