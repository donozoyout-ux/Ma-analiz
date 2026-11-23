import math
from pathlib import Path
from PIL import Image, ImageDraw

def create_mac_analiz_icon(size: int, is_maskable: bool = False) -> Image.Image:
    # 32-bit RGBA image
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Base background: pure white rounded rectangle or circle
    margin = int(size * 0.08) if is_maskable else int(size * 0.04)
    bg_box = [margin, margin, size - margin, size - margin]
    corner_radius = int(size * 0.22) if not is_maskable else 0

    # For maskable, fill entire canvas with white background
    if is_maskable:
        draw.rectangle([0, 0, size, size], fill="#FFFFFF")
    else:
        # Rounded rect with subtle border
        draw.rounded_rectangle(bg_box, radius=corner_radius, fill="#FFFFFF", outline="#F1F5F9", width=max(1, int(size * 0.015)))

    # Inner emblem design: Red badge with dynamic soccer ball / chart bars
    center_x = size / 2.0
    center_y = size / 2.0
    scale = size / 512.0

    # Outer red circle emblem with red gradient vibe
    emblem_radius = 180 * scale
    emblem_box = [
        center_x - emblem_radius,
        center_y - emblem_radius,
        center_x + emblem_radius,
        center_y + emblem_radius,
    ]
    # Draw vibrant red circle
    draw.ellipse(emblem_box, fill="#DC2626")

    # Inside red circle: white stylised soccer ball / analytics graph
    # 1. Bar chart analytics baseline in white
    bar_width = int(24 * scale)
    bar_gap = int(14 * scale)
    base_y = center_y + int(85 * scale)
    bars = [
        (int(45 * scale), "#FEE2E2"),
        (int(90 * scale), "#FFFFFF"),
        (int(135 * scale), "#FFFFFF"),
        (int(175 * scale), "#FEF2F2"),
    ]
    total_bars_width = len(bars) * bar_width + (len(bars) - 1) * bar_gap
    start_bar_x = center_x - total_bars_width / 2.0

    for i, (bar_h, color) in enumerate(bars):
        bx = start_bar_x + i * (bar_width + bar_gap)
        by = base_y - bar_h
        draw.rounded_rectangle(
            [bx, by, bx + bar_width, base_y],
            radius=int(6 * scale),
            fill=color,
        )

    # 2. Dynamic upward trend curve / arrow in white
    arrow_pts = [
        (center_x - 110 * scale, center_y + 20 * scale),
        (center_x - 30 * scale, center_y - 20 * scale),
        (center_x + 50 * scale, center_y - 50 * scale),
        (center_x + 110 * scale, center_y - 95 * scale),
    ]
    draw.line(arrow_pts, fill="#FFFFFF", width=max(2, int(10 * scale)), joint="curve")

    # Arrow head
    head_tip = (center_x + 115 * scale, center_y - 100 * scale)
    draw.polygon([
        head_tip,
        (head_tip[0] - 28 * scale, head_tip[1] + 4 * scale),
        (head_tip[0] - 4 * scale, head_tip[1] + 28 * scale),
    ], fill="#FFFFFF")

    # Star / spark at top left
    spark_cx = center_x - 90 * scale
    spark_cy = center_y - 70 * scale
    draw.ellipse([spark_cx - 8 * scale, spark_cy - 8 * scale, spark_cx + 8 * scale, spark_cy + 8 * scale], fill="#FEF2F2")

    return image


def main():
    root = Path(__file__).resolve().parents[1]
    public_dir = root / "frontend" / "public"
    public_dir.mkdir(parents=True, exist_ok=True)

    sizes = [
        (192, False, "icon-192.png"),
        (512, False, "icon-512.png"),
        (192, True, "icon-maskable-192.png"),
        (512, True, "icon-maskable-512.png"),
        (180, False, "apple-touch-icon.png"),
    ]

    for size, maskable, name in sizes:
        img = create_mac_analiz_icon(size, is_maskable=maskable)
        img.save(public_dir / name, "PNG")
        print(f"Generated {public_dir / name}")

    # Generate android mipmap icons
    res_dir = root / "android" / "app" / "src" / "main" / "res"
    android_sizes = {
        "mipmap-mdpi": 48,
        "mipmap-hdpi": 72,
        "mipmap-xhdpi": 96,
        "mipmap-xxhdpi": 144,
        "mipmap-xxxhdpi": 192,
    }
    for folder, size in android_sizes.items():
        folder_dir = res_dir / folder
        folder_dir.mkdir(parents=True, exist_ok=True)
        # standard launcher
        icon = create_mac_analiz_icon(size, is_maskable=False)
        icon.save(folder_dir / "ic_launcher.png", "PNG")
        # round launcher
        icon_round = create_mac_analiz_icon(size, is_maskable=True)
        icon_round.save(folder_dir / "ic_launcher_round.png", "PNG")
        print(f"Generated Android icons in {folder_dir}")

    # Also generate favicon.svg
    svg_content = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">
  <rect width="100" height="100" rx="22" fill="#FFFFFF"/>
  <circle cx="50" cy="50" r="38" fill="#DC2626"/>
  <rect x="28" y="55" width="8" height="18" rx="2" fill="#FFFFFF"/>
  <rect x="42" y="44" width="8" height="29" rx="2" fill="#FFFFFF"/>
  <rect x="56" y="36" width="8" height="37" rx="2" fill="#FFFFFF"/>
  <path d="M 28 50 L 46 38 L 68 24" fill="none" stroke="#FFFFFF" stroke-width="4" stroke-linecap="round"/>
  <polygon points="72,20 62,23 68,29" fill="#FFFFFF"/>
</svg>'''
    (public_dir / "favicon.svg").write_text(svg_content, encoding="utf-8")
    print(f"Generated {public_dir / 'favicon.svg'}")

if __name__ == "__main__":
    main()
