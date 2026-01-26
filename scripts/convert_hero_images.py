#!/usr/bin/env python3
"""
One-time script to convert all existing hero.png files to optimized hero.jpg.
Also updates summary.json files to reference the new .jpg extension.
"""

import json
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from generators.image_optimizer import optimize_hero_image


def convert_all_hero_images():
    """Convert all hero.png files to hero.jpg and update summary.json references."""
    web_data = Path(__file__).parent.parent / "web" / "data"

    if not web_data.exists():
        print(f"Error: {web_data} does not exist")
        return

    converted = 0
    total_saved = 0

    for date_dir in sorted(web_data.iterdir()):
        if not date_dir.is_dir():
            continue

        png_path = date_dir / "hero.png"
        if not png_path.exists():
            continue

        # Get original size
        original_size = png_path.stat().st_size

        # Convert to JPG
        print(f"Converting {date_dir.name}/hero.png...")
        jpg_path = optimize_hero_image(png_path)
        new_size = jpg_path.stat().st_size

        # Delete original PNG
        png_path.unlink()

        # Calculate savings
        saved = original_size - new_size
        total_saved += saved
        print(f"  → hero.jpg ({original_size / 1024 / 1024:.1f}MB → {new_size / 1024:.0f}KB, saved {saved / 1024 / 1024:.1f}MB)")

        # Update summary.json in web/data
        summary_path = date_dir / "summary.json"
        if summary_path.exists():
            with open(summary_path) as f:
                summary = json.load(f)
            if "hero_image_url" in summary:
                summary["hero_image_url"] = summary["hero_image_url"].replace(".png", ".jpg")
                with open(summary_path, "w") as f:
                    json.dump(summary, f, indent=2)
                print(f"  → Updated {summary_path.name}")

        converted += 1

    print(f"\nDone! Converted {converted} images, saved {total_saved / 1024 / 1024:.1f}MB total")


if __name__ == "__main__":
    convert_all_hero_images()
