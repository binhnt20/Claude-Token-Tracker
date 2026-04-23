#!/usr/bin/env python3
"""Generate app icon (icon.png + icon.icns) using macOS AppKit."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).parent
ICON_DIR = ROOT / "assets"


def generate():
    from AppKit import (
        NSImage, NSBitmapImageRep, NSGraphicsContext, NSColor,
        NSFont, NSString, NSMakeRect, NSFontAttributeName,
        NSForegroundColorAttributeName, NSBezierPath,
        NSPNGFileType,
    )
    from Foundation import NSMakePoint, NSDictionary

    size = 512
    img = NSImage.alloc().initWithSize_((size, size))
    img.lockFocus()

    # Background: rounded rect with gradient-like dark blue
    bg = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.06, 0.09, 0.16, 1.0)
    bg.setFill()
    path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(0, 0, size, size), 90, 90
    )
    path.fill()

    # Accent ring
    ring = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.51, 0.55, 0.97, 0.3)
    ring.setStroke()
    inner = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(24, 24, size - 48, size - 48), 72, 72
    )
    inner.setLineWidth_(3)
    inner.stroke()

    # Bar chart bars (3 bars representing token usage)
    bars = [
        (140, 0.45, (0.20, 0.83, 0.60)),   # green - sonnet
        (230, 0.72, (0.51, 0.55, 0.97)),    # purple - opus
        (320, 0.35, (0.98, 0.57, 0.24)),    # orange - haiku
    ]
    bar_w = 60
    base_y = 120
    max_h = 260

    for x, pct, (r, g, b) in bars:
        h = max_h * pct
        c = NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1.0)
        c.setFill()
        bar_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(x, base_y, bar_w, h), 8, 8
        )
        bar_path.fill()

    # "CT" text at top
    text_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.95, 0.96, 0.98, 1.0)
    font = NSFont.fontWithName_size_("Helvetica-Bold", 72)
    attrs = NSDictionary.dictionaryWithObjectsAndKeys_(
        font, NSFontAttributeName,
        text_color, NSForegroundColorAttributeName,
        None,
    )
    text = NSString.stringWithString_("CT")
    text_size = text.sizeWithAttributes_(attrs)
    tx = (size - text_size.width) / 2
    text.drawAtPoint_withAttributes_(NSMakePoint(tx, 400), attrs)

    img.unlockFocus()

    # Save as PNG
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    png_path = ICON_DIR / "icon.png"

    tiff = img.TIFFRepresentation()
    rep = NSBitmapImageRep.imageRepWithData_(tiff)
    png_data = rep.representationUsingType_properties_(NSPNGFileType, None)
    png_data.writeToFile_atomically_(str(png_path), True)
    print(f"  PNG: {png_path}")

    # Convert to .icns using iconutil
    iconset = ICON_DIR / "icon.iconset"
    iconset.mkdir(exist_ok=True)

    for sz in [16, 32, 64, 128, 256, 512]:
        for scale in [1, 2]:
            px = sz * scale
            suffix = f"{sz}x{sz}@2x" if scale == 2 else f"{sz}x{sz}"
            out = iconset / f"icon_{suffix}.png"
            subprocess.run(
                ["sips", "-z", str(px), str(px), str(png_path), "--out", str(out)],
                capture_output=True,
            )

    icns_path = ICON_DIR / "icon.icns"
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns_path)], check=True)

    # Cleanup iconset
    import shutil
    shutil.rmtree(iconset)

    print(f"  ICNS: {icns_path}")


if __name__ == "__main__":
    print("Generating app icon...")
    generate()
    print("Done!")
