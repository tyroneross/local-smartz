"""Generate the Local Smartz app icon.

Design: rounded-square indigo card with a centered magnifying glass whose handle
tapers into the top-right corner; a small square "chip" dot inside the lens
hints at local inference. Flat, two-color, recognizable at 16px.

Output: `app/LocalSmartz/Assets.xcassets/AppIcon.appiconset/*.png` at all sizes
needed by macOS, plus `AppIcon.icns` at `app/build/AppIcon.icns`.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

# --- palette ---------------------------------------------------------------
BG_TOP = (79, 70, 229)     # indigo-600
BG_BOT = (49, 46, 129)     # indigo-900
LENS = (242, 244, 255)     # near-white
LENS_SHADOW = (30, 27, 75) # deep indigo
CHIP = (16, 185, 129)      # emerald-500 (local inference signal)
HANDLE = (242, 244, 255)

MASTER = 1024  # master render size; macOS sizes down via sips


def _vertical_gradient(size: int, top: tuple[int, int, int], bot: tuple[int, int, int]) -> Image.Image:
    img = Image.new("RGB", (size, size), top)
    px = img.load()
    for y in range(size):
        t = y / (size - 1)
        r = round(top[0] + (bot[0] - top[0]) * t)
        g = round(top[1] + (bot[1] - top[1]) * t)
        b = round(top[2] + (bot[2] - top[2]) * t)
        for x in range(size):
            px[x, y] = (r, g, b)
    return img


def _rounded_mask(size: int, radius_frac: float = 0.225) -> Image.Image:
    """macOS squircle-ish — good enough with a simple rounded rect."""
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    r = int(size * radius_frac)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=r, fill=255)
    return mask


def render_icon(size: int = MASTER) -> Image.Image:
    """Render the full icon at `size`×`size`."""
    # 1. background card
    bg = _vertical_gradient(size, BG_TOP, BG_BOT)

    # 2. subtle inner highlight (very top band, barely visible)
    hl = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(hl)
    d.rounded_rectangle(
        (int(size * 0.06), int(size * 0.06), int(size * 0.94), int(size * 0.42)),
        radius=int(size * 0.18),
        fill=(255, 255, 255, 28),
    )
    bg = bg.convert("RGBA")
    bg.alpha_composite(hl)

    # 3. magnifying glass
    cx, cy = int(size * 0.44), int(size * 0.44)
    lens_r = int(size * 0.24)
    ring_w = int(size * 0.055)

    # shadow pass (soft offset)
    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ds = ImageDraw.Draw(shadow)
    ds.ellipse(
        (cx - lens_r - ring_w + int(size * 0.01),
         cy - lens_r - ring_w + int(size * 0.02),
         cx + lens_r + ring_w + int(size * 0.01),
         cy + lens_r + ring_w + int(size * 0.02)),
        fill=(*LENS_SHADOW, 120),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=size * 0.012))
    bg.alpha_composite(shadow)

    fg = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    df = ImageDraw.Draw(fg)

    # ring
    df.ellipse(
        (cx - lens_r - ring_w, cy - lens_r - ring_w,
         cx + lens_r + ring_w, cy + lens_r + ring_w),
        fill=LENS,
    )
    # inner lens area (the "screen")
    df.ellipse(
        (cx - lens_r, cy - lens_r, cx + lens_r, cy + lens_r),
        fill=LENS_SHADOW,
    )
    # chip dot — signals local inference (a little data "atom" inside the lens)
    chip_r = int(size * 0.072)
    df.rounded_rectangle(
        (cx - chip_r, cy - chip_r, cx + chip_r, cy + chip_r),
        radius=int(chip_r * 0.35),
        fill=CHIP,
    )
    # four leads coming off the chip — tiny, emphasize "chip" read
    lead_len = int(size * 0.048)
    lead_w = int(size * 0.018)
    for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
        x0 = cx + dx * (chip_r + lead_len // 2) - (lead_w // 2 if dy else lead_len // 2)
        y0 = cy + dy * (chip_r + lead_len // 2) - (lead_w // 2 if dx else lead_len // 2)
        x1 = x0 + (lead_w if dy else lead_len)
        y1 = y0 + (lead_w if dx else lead_len)
        df.rectangle((x0, y0, x1, y1), fill=CHIP)

    # handle — from the lower-right of the lens toward the bottom-right corner
    angle = math.radians(40)  # 40° down-right from horizontal
    # handle endpoints (anchor on ring edge)
    ax = cx + int((lens_r + ring_w) * math.cos(angle))
    ay = cy + int((lens_r + ring_w) * math.sin(angle))
    bx = cx + int((lens_r + ring_w + size * 0.32) * math.cos(angle))
    by = cy + int((lens_r + ring_w + size * 0.32) * math.sin(angle))
    handle_w = int(size * 0.075)
    df.line((ax, ay, bx, by), fill=HANDLE, width=handle_w)
    # rounded caps
    df.ellipse((ax - handle_w // 2, ay - handle_w // 2, ax + handle_w // 2, ay + handle_w // 2), fill=HANDLE)
    df.ellipse((bx - handle_w // 2, by - handle_w // 2, bx + handle_w // 2, by + handle_w // 2), fill=HANDLE)

    bg.alpha_composite(fg)

    # 4. clip to rounded-rect mask (macOS card shape)
    mask = _rounded_mask(size)
    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(bg, (0, 0), mask=mask)
    return result


# Required icon sizes for macOS .icns
ICON_SIZES = [16, 32, 64, 128, 256, 512, 1024]
# Apple Contents.json expects 1x and 2x variants for each "size" point
APPICON_ENTRIES = [
    # (filename, point-size, scale)
    ("icon_16x16.png", 16, 1),
    ("icon_16x16@2x.png", 16, 2),
    ("icon_32x32.png", 32, 1),
    ("icon_32x32@2x.png", 32, 2),
    ("icon_128x128.png", 128, 1),
    ("icon_128x128@2x.png", 128, 2),
    ("icon_256x256.png", 256, 1),
    ("icon_256x256@2x.png", 256, 2),
    ("icon_512x512.png", 512, 1),
    ("icon_512x512@2x.png", 512, 2),
]


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    app_dir = script_dir.parent
    assets_dir = app_dir / "LocalSmartz" / "Assets.xcassets" / "AppIcon.appiconset"
    assets_dir.mkdir(parents=True, exist_ok=True)
    build_dir = app_dir / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    iconset_dir = build_dir / "AppIcon.iconset"
    iconset_dir.mkdir(parents=True, exist_ok=True)

    master = render_icon(MASTER)

    # Emit every size used by both Assets.xcassets and iconutil's iconset format
    for fname, point, scale in APPICON_ENTRIES:
        px = point * scale
        img = master.resize((px, px), resample=Image.LANCZOS)
        img.save(assets_dir / fname)
        # iconutil expects files named: icon_<size>x<size>.png and icon_<size>x<size>@2x.png
        img.save(iconset_dir / fname)

    # Also save a 1024 master under build/ for reference
    master.save(build_dir / "AppIcon-1024.png")

    # Write Contents.json
    import json
    contents = {
        "images": [
            {
                "filename": fname,
                "idiom": "mac",
                "scale": f"{scale}x",
                "size": f"{point}x{point}",
            }
            for fname, point, scale in APPICON_ENTRIES
        ],
        "info": {"author": "xcode", "version": 1},
    }
    (assets_dir / "Contents.json").write_text(json.dumps(contents, indent=2) + "\n")

    # Contents.json at Assets.xcassets root (required)
    (assets_dir.parent / "Contents.json").write_text(
        json.dumps({"info": {"author": "xcode", "version": 1}}, indent=2) + "\n"
    )

    print(f"✓ Icon assets written to {assets_dir}")
    print(f"✓ Iconset written to {iconset_dir}")
    print(f"✓ Master 1024 at {build_dir / 'AppIcon-1024.png'}")


if __name__ == "__main__":
    main()
