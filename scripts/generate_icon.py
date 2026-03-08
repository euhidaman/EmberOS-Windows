"""Generate the EmberOS tray icon (assets/icon.ico)."""

from pathlib import Path

def generate_icon():
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("Pillow not available — icon will be generated at runtime.")
        return

    sizes = [16, 32, 48, 64, 128, 256]
    images = []

    for sz in sizes:
        img = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        m = max(1, sz // 16)  # margin scale

        # Background circle
        draw.ellipse([m, m, sz - m - 1, sz - m - 1], fill=(200, 80, 30, 255))

        # Inner "E" letter
        lx = sz // 4
        rx = sz * 3 // 4
        ty = sz // 4
        by = sz * 3 // 4
        bar_h = max(1, sz // 10)
        bar_w = max(1, sz // 8)

        # Vertical bar of E
        draw.rectangle([lx, ty, lx + bar_w, by], fill=(255, 255, 255, 255))
        # Top horizontal
        draw.rectangle([lx, ty, rx, ty + bar_h], fill=(255, 255, 255, 255))
        # Middle horizontal
        mid_y = (ty + by) // 2 - bar_h // 2
        draw.rectangle([lx, mid_y, rx - bar_w, mid_y + bar_h], fill=(255, 255, 255, 255))
        # Bottom horizontal
        draw.rectangle([lx, by - bar_h, rx, by], fill=(255, 255, 255, 255))

        images.append(img)

    out = Path(__file__).resolve().parent.parent / "assets" / "icon.ico"
    out.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(str(out), format="ICO", sizes=[(s, s) for s in sizes],
                   append_images=images[1:])
    print(f"Icon saved to {out}")


if __name__ == "__main__":
    generate_icon()
