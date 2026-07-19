from __future__ import annotations

from pathlib import Path
from collections import deque

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "assets"
ICON_SIZES = [16, 24, 32, 48, 64, 128, 256]
YUZU_APP_SOURCE_NAME = "yuzu_app_source.png"

COLORS = {
    "tray_stopped.ico": ("#94a3b8", "#475569"),
    "tray_checking.ico": ("#f59e0b", "#b45309"),
    "tray_online.ico": ("#22c55e", "#15803d"),
    "tray_offline.ico": ("#ef4444", "#b91c1c"),
    "tray_error.ico": ("#7f1d1d", "#450a0a"),
}


def make_icon(path: Path, fill: str, border: str) -> None:
    images = []
    for size in ICON_SIZES:
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        margin = max(2, size // 10)
        draw.ellipse(
            (margin, margin, size - margin, size - margin),
            fill=fill,
            outline=border,
            width=max(1, size // 14),
        )
        inner = size // 3
        draw.ellipse(
            (inner, inner, size - inner, size - inner),
            fill="#ffffff",
        )
        images.append(image)
    images[0].save(
        path,
        sizes=[(size, size) for size in ICON_SIZES],
        append_images=images[1:],
    )


def make_dark_corners_transparent(image: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    pixels = rgba.load()
    for y in range(rgba.height):
        for x in range(rgba.width):
            red, green, blue, alpha = pixels[x, y]
            if red <= 3 and green <= 3 and blue <= 3:
                pixels[x, y] = (red, green, blue, 0)
    return rgba


def is_light_checker_pixel(red: int, green: int, blue: int) -> bool:
    return min(red, green, blue) >= 225 and max(red, green, blue) - min(red, green, blue) <= 30


def remove_light_checkerboard_background(image: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    pixels = rgba.load()
    visited = set()
    queue = deque()

    def enqueue_if_background(x: int, y: int) -> None:
        if (x, y) in visited:
            return
        red, green, blue, alpha = pixels[x, y]
        if alpha == 0 or is_light_checker_pixel(red, green, blue):
            visited.add((x, y))
            queue.append((x, y))

    for x in range(rgba.width):
        enqueue_if_background(x, 0)
        enqueue_if_background(x, rgba.height - 1)
    for y in range(rgba.height):
        enqueue_if_background(0, y)
        enqueue_if_background(rgba.width - 1, y)

    while queue:
        x, y = queue.popleft()
        for next_x, next_y in (
            (x - 1, y),
            (x + 1, y),
            (x, y - 1),
            (x, y + 1),
        ):
            if 0 <= next_x < rgba.width and 0 <= next_y < rgba.height:
                enqueue_if_background(next_x, next_y)

    for x, y in visited:
        red, green, blue, _alpha = pixels[x, y]
        pixels[x, y] = (red, green, blue, 0)
    return rgba


def crop_to_alpha_content(image: Image.Image, padding_ratio: float = 0.08) -> Image.Image:
    alpha = image.getchannel("A")
    bbox = alpha.getbbox()
    if bbox is None:
        return image
    left, top, right, bottom = bbox
    padding = int(max(right - left, bottom - top) * padding_ratio)
    left = max(0, left - padding)
    top = max(0, top - padding)
    right = min(image.width, right + padding)
    bottom = min(image.height, bottom + padding)
    return image.crop((left, top, right, bottom))


def fit_square_icon(source: Image.Image, size: int) -> Image.Image:
    image = source.copy()
    image.thumbnail((size, size), Image.Resampling.LANCZOS)
    output = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    output.alpha_composite(
        image,
        ((size - image.width) // 2, (size - image.height) // 2),
    )
    return output


def make_yuzu_icon_images(asset_dir: Path) -> list[Image.Image]:
    source_path = asset_dir / YUZU_APP_SOURCE_NAME
    if not source_path.exists():
        raise FileNotFoundError(f"Missing generated yuzu icon source: {source_path}")
    source = crop_to_alpha_content(
        make_dark_corners_transparent(
            remove_light_checkerboard_background(Image.open(source_path))
        )
    )
    return [fit_square_icon(source, size) for size in ICON_SIZES]


def draw_yuzu(size: int) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    fruit_box = (
        int(size * 0.15),
        int(size * 0.22),
        int(size * 0.85),
        int(size * 0.90),
    )
    draw.ellipse(fruit_box, fill="#ffd84a", outline="#d99112", width=max(1, size // 32))

    # Peel texture and shine.
    draw.ellipse(
        (
            int(size * 0.28),
            int(size * 0.33),
            int(size * 0.45),
            int(size * 0.47),
        ),
        fill="#ffe989",
    )
    for x, y in (
        (0.35, 0.58),
        (0.55, 0.38),
        (0.63, 0.64),
        (0.42, 0.76),
        (0.72, 0.52),
    ):
        r = max(1, size // 42)
        draw.ellipse(
            (
                int(size * x) - r,
                int(size * y) - r,
                int(size * x) + r,
                int(size * y) + r,
            ),
            fill="#efb92f",
        )

    leaf_box = (
        int(size * 0.45),
        int(size * 0.05),
        int(size * 0.88),
        int(size * 0.33),
    )
    draw.ellipse(leaf_box, fill="#80b34a", outline="#5c842e", width=max(1, size // 38))
    draw.line(
        (
            int(size * 0.54),
            int(size * 0.25),
            int(size * 0.82),
            int(size * 0.14),
        ),
        fill="#d8ee9b",
        width=max(1, size // 36),
    )
    draw.rounded_rectangle(
        (
            int(size * 0.44),
            int(size * 0.17),
            int(size * 0.54),
            int(size * 0.33),
        ),
        radius=max(1, size // 32),
        fill="#8b6a23",
    )

    eye_r = max(2, size // 18)
    for x in (0.39, 0.62):
        draw.ellipse(
            (
                int(size * x) - eye_r,
                int(size * 0.57) - eye_r,
                int(size * x) + eye_r,
                int(size * 0.57) + eye_r,
            ),
            fill="#3f2a13",
        )
        sparkle = max(1, size // 56)
        draw.ellipse(
            (
                int(size * x) - sparkle,
                int(size * 0.54) - sparkle,
                int(size * x) + sparkle,
                int(size * 0.54) + sparkle,
            ),
            fill="#ffffff",
        )

    cheek_r = max(2, size // 14)
    for x in (0.28, 0.73):
        draw.ellipse(
            (
                int(size * x) - cheek_r,
                int(size * 0.68) - cheek_r,
                int(size * x) + cheek_r,
                int(size * 0.68) + cheek_r,
            ),
            fill="#ffad78",
        )
    draw.arc(
        (
            int(size * 0.45),
            int(size * 0.60),
            int(size * 0.57),
            int(size * 0.74),
        ),
        start=10,
        end=170,
        fill="#6b3a16",
        width=max(1, size // 28),
    )
    return image


def make_yuzu_app_assets(asset_dir: Path) -> None:
    images = make_yuzu_icon_images(asset_dir)
    icon_png = images[-1]
    icon_png.save(asset_dir / "yuzu_app.png", optimize=True)
    images[0].save(
        asset_dir / "yuzu_app.ico",
        sizes=[(size, size) for size in ICON_SIZES],
        append_images=images[1:],
    )


def main() -> int:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    for name, (fill, border) in COLORS.items():
        make_icon(ASSET_DIR / name, fill, border)
    make_yuzu_app_assets(ASSET_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
