"""Image optimization utilities for hero images."""

from pathlib import Path
from PIL import Image


def optimize_hero_image(
    input_path: Path,
    output_path: Path = None,
    max_width: int = 1280,
    quality: int = 75
) -> Path:
    """
    Convert PNG to optimized WebP with resize.

    Args:
        input_path: Path to input image (PNG)
        output_path: Path for output image. If None, uses input path with .webp extension
        max_width: Maximum width in pixels (maintains aspect ratio)
        quality: WebP quality (1-100)

    Returns:
        Path to the optimized image
    """
    input_path = Path(input_path)

    if output_path is None:
        output_path = input_path.with_suffix('.webp')
    else:
        output_path = Path(output_path)

    with Image.open(input_path) as img:
        # Resize if wider than max_width
        if img.width > max_width:
            ratio = max_width / img.width
            new_size = (max_width, int(img.height * ratio))
            img = img.resize(new_size, Image.LANCZOS)

        # Convert to RGB (handles RGBA/P modes)
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')

        # Save as optimized WebP
        img.save(output_path, 'WEBP', quality=quality, optimize=True)

    return output_path
