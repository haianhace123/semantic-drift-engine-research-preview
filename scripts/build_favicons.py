from pathlib import Path
from math import sqrt

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
BRAND_DIR = ROOT / "src" / "static" / "assets" / "brand"


def color_distance(pixel, background):
    r, g, b = pixel[:3]
    br, bg, bb = background

    return sqrt(
        (r - br) ** 2
        + (g - bg) ** 2
        + (b - bb) ** 2
    )


def estimate_background(image):
    """
    Estimate the background color from several points near the corners.

    We intentionally do not rely on a single corner pixel because the
    generated PNG masters can contain subtle gradients/noise.
    """
    width, height = image.size

    points = [
        (2, 2),
        (width - 3, 2),
        (2, height - 3),
        (width - 3, height - 3),

        (width // 8, height // 8),
        (width - width // 8 - 1, height // 8),
        (width // 8, height - height // 8 - 1),
        (
            width - width // 8 - 1,
            height - height // 8 - 1,
        ),
    ]

    colors = [
        image.getpixel(point)[:3]
        for point in points
    ]

    red = sorted(color[0] for color in colors)
    green = sorted(color[1] for color in colors)
    blue = sorted(color[2] for color in colors)

    middle = len(colors) // 2

    return (
        red[middle],
        green[middle],
        blue[middle],
    )


def remove_background(image):
    """
    Convert the original solid/near-solid background into transparency.

    DISTANCE <= 18:
        completely transparent

    DISTANCE 18..52:
        feathered alpha for anti-aliased edges

    DISTANCE >= 52:
        preserve original artwork
    """
    image = image.convert("RGBA")

    background = estimate_background(image)

    pixels = image.load()

    transparent_distance = 18.0
    opaque_distance = 52.0

    for y in range(image.height):
        for x in range(image.width):
            r, g, b, original_alpha = pixels[x, y]

            distance = color_distance(
                (r, g, b),
                background,
            )

            if distance <= transparent_distance:
                alpha = 0

            elif distance >= opaque_distance:
                alpha = original_alpha

            else:
                ratio = (
                    distance - transparent_distance
                ) / (
                    opaque_distance
                    - transparent_distance
                )

                alpha = int(
                    original_alpha * ratio
                )

            pixels[x, y] = (
                r,
                g,
                b,
                alpha,
            )

    return image


def crop_master(image):
    """
    First crop 16% from every side.

    This is intentional: the master PNGs contain generous surrounding
    space and a favicon needs the mark to occupy most of its 16x16 area.
    """
    width, height = image.size

    crop_amount = int(
        min(width, height) * 0.16
    )

    return image.crop(
        (
            crop_amount,
            crop_amount,
            width - crop_amount,
            height - crop_amount,
        )
    )


def crop_to_visible_artwork(image):
    """
    Crop again based on the actual alpha channel after background removal.
    """
    alpha = image.getchannel("A")

    # Ignore extremely faint pixels left from the source background.
    thresholded = alpha.point(
        lambda value: 255 if value >= 12 else 0
    )

    bbox = thresholded.getbbox()

    if bbox is None:
        raise RuntimeError(
            "Could not detect visible logo artwork."
        )

    return image.crop(bbox)


def make_square_with_padding(image):
    """
    Place the mark on a transparent square canvas with only 2.5% padding.

    Small padding keeps the favicon visually large without allowing the
    artwork to touch the browser favicon bounds.
    """
    width, height = image.size

    longest_side = max(width, height)

    padding = max(
        1,
        int(longest_side * 0.025),
    )

    canvas_side = (
        longest_side
        + padding * 2
    )

    canvas = Image.new(
        "RGBA",
        (
            canvas_side,
            canvas_side,
        ),
        (
            0,
            0,
            0,
            0,
        ),
    )

    x = (
        canvas_side - width
    ) // 2

    y = (
        canvas_side - height
    ) // 2

    canvas.alpha_composite(
        image,
        (
            x,
            y,
        ),
    )

    return canvas


def build_favicon(
    source_name,
    output_name,
):
    source_path = (
        BRAND_DIR / source_name
    )

    output_path = (
        BRAND_DIR / output_name
    )

    if not source_path.exists():
        raise FileNotFoundError(
            f"Missing source logo: {source_path}"
        )

    original = Image.open(
        source_path
    ).convert("RGBA")

    print(
        f"{source_name}: "
        f"original={original.size}"
    )

    image = crop_master(original)

    print(
        f"{source_name}: "
        f"after initial crop={image.size}"
    )

    image = remove_background(
        image
    )

    image = crop_to_visible_artwork(
        image
    )

    print(
        f"{source_name}: "
        f"visible artwork={image.size}"
    )

    image = make_square_with_padding(
        image
    )

    print(
        f"{source_name}: "
        f"favicon canvas={image.size}"
    )

    image.save(
        output_path,
        format="ICO",
        sizes=[
            (16, 16),
            (32, 32),
            (48, 48),
            (64, 64),
        ],
    )

    print(
        f"CREATED: {output_path}"
    )


def main():
    BRAND_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    # Bright / silver mark for dark browser UI.
    build_favicon(
        "logo-dark.png",
        "favicon-dark.ico",
    )

    # Graphite / dark mark for light browser UI.
    build_favicon(
        "logo-light.png",
        "favicon-light.ico",
    )

    print("")
    print("Favicons completed.")
    print(
        "dark tab  -> favicon-dark.ico"
    )
    print(
        "light tab -> favicon-light.ico"
    )


if __name__ == "__main__":
    main()