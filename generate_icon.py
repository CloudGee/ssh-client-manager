#!/usr/bin/env python3
"""Generate the SSH Client Manager app icon as a 1024x1024 PNG,
then convert to macOS .icns via iconutil."""

import math
import subprocess
import tempfile
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def draw_icon(size=1024):
    """Draw a flat, minimal SSH Client Manager icon."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    s = size / 1024  # scale factor
    cx, cy = size / 2, size / 2

    # ── Rounded square background — flat dark blue ──
    pad = int(24 * s)
    rr = int(200 * s)
    draw.rounded_rectangle(
        [pad, pad, size - pad, size - pad],
        radius=rr,
        fill=(22, 48, 72, 255),
    )

    # ── "SSH" text — centered, bold, bright cyan ──
    ssh_size = int(320 * s)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/SFMono-Bold.otf", ssh_size)
    except (OSError, IOError):
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", ssh_size)
        except (OSError, IOError):
            font = ImageFont.load_default()

    text = "SSH"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = cx - tw / 2
    ty = cy - th / 2

    draw.text((tx, ty), text, fill=(80, 230, 255, 255), font=font)

    return img


def create_icns(png_path, icns_path):
    """Convert a 1024x1024 PNG to macOS .icns using iconutil."""
    iconset_dir = tempfile.mkdtemp(suffix=".iconset")

    img = Image.open(png_path)

    # Required sizes for .iconset
    sizes = [16, 32, 64, 128, 256, 512, 1024]
    for sz in sizes:
        resized = img.resize((sz, sz), Image.LANCZOS)
        resized.save(os.path.join(iconset_dir, f"icon_{sz}x{sz}.png"))
        if sz <= 512:
            # @2x variant: e.g. icon_16x16@2x.png is 32px
            doubled = img.resize((sz * 2, sz * 2), Image.LANCZOS)
            doubled.save(os.path.join(iconset_dir, f"icon_{sz}x{sz}@2x.png"))

    # Rename iconset_dir to have .iconset extension
    iconset_path = iconset_dir.rstrip("/")
    proper_path = iconset_path + "_renamed.iconset"
    os.rename(iconset_path, proper_path)

    subprocess.run(["iconutil", "-c", "icns", proper_path, "-o", icns_path], check=True)

    # Cleanup
    import shutil

    shutil.rmtree(proper_path)
    print(f"✅ Created: {icns_path}")


if __name__ == "__main__":
    root = Path(__file__).parent
    png_path = root / "packaging" / "macos" / "sshclientmanager.png"
    icns_path = root / "packaging" / "macos" / "sshclientmanager.icns"

    print("🎨 Generating icon...")
    icon = draw_icon(1024)
    icon.save(str(png_path), "PNG")
    print(f"✅ PNG saved: {png_path}")

    print("📦 Converting to .icns...")
    create_icns(str(png_path), str(icns_path))
    print("🎉 Done!")
