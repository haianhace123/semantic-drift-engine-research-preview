from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
BRAND_DIR = ROOT / "src" / "static" / "assets" / "brand"

SOURCE = BRAND_DIR / "logo-light.png"
OUTPUT = BRAND_DIR / "favicon-circle.ico"


def crop_center_square(image, crop_ratio=0.16):
    """
    Crop the same percentage from all four sides.

    The master logo contains generous outer spacing, so the favicon
    needs a tighter crop to make the mark appear larger in browser tabs.
    """
    width, height = image.size

    crop_amount = int(
        min(width, height) * crop_ratio
    )

    return image.crop(
        (
            crop_amount,
            crop_amount,
            width - crop_amount,
            height - crop_amount,
        )
    )


def apply_circle_mask(image):
    """
    Keep the original logo pixels inside a circular mask.

    Everything outside the circle becomes fully transparent.
    """
    image = image.convert("RGBA")

    size = min(
        image.width,
        image.height,
    )

    left = (
        image.width - size
    ) // 2

    top = (
        image.height - size
    ) // 2

    image = image.crop(
        (
            left,
            top,
            left + size,
            top + size,
        )
    )

    mask = Image.new(
        "L",
        (
            size,
            size,
        ),
        0,
    )

    draw = ImageDraw.Draw(mask)

    inset = max(
        1,
        int(size * 0.01),
    )

    draw.ellipse(
        (
            inset,
            inset,
            size - inset - 1,
            size - inset - 1,
        ),
        fill=255,
    )

    image.putalpha(mask)

    return image


def main():
    if not SOURCE.exists():
        raise FileNotFoundError(
            f"Missing source logo: {SOURCE}"
        )

    image = Image.open(
        SOURCE
    ).convert("RGBA")

    print(
        f"Original size: {image.size}"
    )

    image = crop_center_square(
        image,
        crop_ratio=0.10,
    )

    print(
        f"After crop: {image.size}"
    )

    image = apply_circle_mask(
        image
    )

    print(
        f"Circle canvas: {image.size}"
    )

    image.save(
        OUTPUT,
        format="ICO",
        sizes=[
            (16, 16),
            (32, 32),
            (48, 48),
            (64, 64),
        ],
    )

    print(
        f"Created: {OUTPUT}"
    )


if __name__ == "__main__":
    main()