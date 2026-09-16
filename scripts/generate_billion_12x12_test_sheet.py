#!/usr/bin/env python3
"""Generate the deterministic 12-inch print test sheet for the billion render.

The source PNG is untagged, but its renderer explicitly produces sRGB-encoded
samples (Lab -> XYZ D65 -> sRGB transfer function). This script preserves those
RGB samples without a color conversion and embeds a fixed standard sRGB ICC
profile in both outputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from PIL import Image, ImageCms, ImageDraw, ImageFont, ImageStat, PngImagePlugin


ROOT = Path(__file__).resolve().parents[2]
SOURCE = (
    ROOT
    / "shufflesnap-paper/benchmarks/results/renders/"
    / "billion_shufflesnap_31623_seed0_test_interp75.png"
)
OUTPUT_DIR = ROOT / "shufflesnap-print/outputs"
PNG_OUTPUT = OUTPUT_DIR / "billion_12x12_print_test_sheet_300ppi.png"
JPEG_OUTPUT = OUTPUT_DIR / "billion_12x12_print_test_sheet_300ppi.jpg"

ICC_PROFILE = Path("/usr/share/color/icc/colord/sRGB.icc")
FONT_REGULAR = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
FONT_BOLD = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
FONT_MONO = Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")

CANVAS_PX = 3600
PPI = 300
TRIM_INSET_MM = 3.0
TRIM_INSET_PX_EXACT = TRIM_INSET_MM / 25.4 * PPI
TRIM_INSET_PX = round(TRIM_INSET_PX_EXACT)

BACKGROUND = (247, 247, 244)
INK = (29, 31, 34)
MUTED = (89, 94, 99)
RULE = (170, 174, 176)
PANEL = (255, 255, 255)
ACCENT = (0, 127, 155)


@dataclass(frozen=True)
class CropSpec:
    label: str
    center: tuple[int, int]


SQUARE_CROPS = (
    CropSpec("CYAN FINE VEINS", (1180, 7000)),
    CropSpec("ORANGE TEXTURE", (7550, 820)),
    CropSpec("PINK FRACTURES", (8350, 3150)),
    CropSpec("LOW-CONTRAST CENTER", (5250, 5050)),
)

WIDE_CROPS = (
    CropSpec("CYAN → LAVENDER GRADIENT", (3000, 5000)),
    CropSpec("LOWER LAVENDER BRANCH DETAIL", (6200, 8250)),
)

ART_PATCHES = (
    ("cyan", (900, 7000)),
    ("aqua", (1600, 6000)),
    ("sky", (3000, 8000)),
    ("lavender", (6000, 8200)),
    ("pink", (8500, 5000)),
    ("coral", (6500, 2500)),
    ("orange", (8000, 700)),
    ("gold", (3000, 1200)),
)

NEUTRAL_LEVELS = (255, 245, 230, 210, 180, 150, 120, 90, 60, 30)


def font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    if not path.is_file():
        raise FileNotFoundError(f"Required font not found: {path}")
    return ImageFont.truetype(str(path), size=size)


F_TITLE = font(FONT_BOLD, 72)
F_SUBTITLE = font(FONT_REGULAR, 34)
F_LABEL = font(FONT_BOLD, 32)
F_SMALL = font(FONT_REGULAR, 28)
F_SMALL_BOLD = font(FONT_BOLD, 29)
F_PATCH = font(FONT_REGULAR, 24)
F_MONO = font(FONT_MONO, 24)


def text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], value: str,
         *, typeface: ImageFont.ImageFont, fill: tuple[int, int, int] = INK,
         anchor: str | None = None) -> None:
    draw.text(xy, value, font=typeface, fill=fill, anchor=anchor)


def wrap_text(draw: ImageDraw.ImageDraw, value: str, typeface: ImageFont.ImageFont,
              max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in value.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words.pop(0)
        for word in words:
            trial = f"{current} {word}"
            if draw.textlength(trial, font=typeface) <= max_width:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def draw_wrapped(draw: ImageDraw.ImageDraw, xy: tuple[int, int], value: str,
                 *, typeface: ImageFont.ImageFont, max_width: int,
                 line_height: int, fill: tuple[int, int, int] = INK) -> int:
    x, y = xy
    for line in wrap_text(draw, value, typeface, max_width):
        text(draw, (x, y), line, typeface=typeface, fill=fill)
        y += line_height
    return y


def crop_at_physical_scale(source: Image.Image, center: tuple[int, int],
                           output_size: tuple[int, int]) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """Crop at the 30-inch master's physical scale and resample to 300 ppi.

    The master is 333.33 ppi, so ten source pixels correspond to nine output
    pixels. The selected output dimensions are multiples of nine, making the
    source crop dimensions exact integers.
    """
    out_w, out_h = output_size
    if out_w % 9 or out_h % 9:
        raise ValueError("Physical-scale output dimensions must be divisible by 9")
    src_w = out_w * 10 // 9
    src_h = out_h * 10 // 9
    cx, cy = center
    left = cx - src_w // 2
    top = cy - src_h // 2
    box = (left, top, left + src_w, top + src_h)
    if box[0] < 0 or box[1] < 0 or box[2] > source.width or box[3] > source.height:
        raise ValueError(f"Crop {box} falls outside source {source.size}")
    tile = source.crop(box).resize(output_size, Image.Resampling.LANCZOS)
    return tile, box


def paste_tile(canvas: Image.Image, draw: ImageDraw.ImageDraw, tile: Image.Image,
               xy: tuple[int, int], label: str, *, box: tuple[int, int, int, int],
               label_y: int) -> None:
    x, y = xy
    draw.rectangle((x - 2, y - 2, x + tile.width + 1, y + tile.height + 1),
                   fill=PANEL, outline=RULE, width=2)
    canvas.paste(tile, (x, y))
    text(draw, (x, label_y), label, typeface=F_LABEL)
    dims = f"source {box[2] - box[0]}×{box[3] - box[1]} px → proof {tile.width}×{tile.height} px"
    text(draw, (x, label_y + 39), dims, typeface=F_MONO, fill=MUTED)


def average_patch(source: Image.Image, center: tuple[int, int], radius: int = 40) -> tuple[int, int, int]:
    x, y = center
    region = source.crop((x - radius, y - radius, x + radius + 1, y + radius + 1))
    return tuple(round(v) for v in ImageStat.Stat(region).mean)  # type: ignore[return-value]


def draw_art_patches(canvas: Image.Image, draw: ImageDraw.ImageDraw,
                     source: Image.Image) -> None:
    x0, y0 = 100, 2765
    text(draw, (x0, y0), "ARTWORK-DERIVED sRGB PATCHES", typeface=F_SMALL_BOLD)
    patch_y = y0 + 53
    patch_w, patch_h, gap = 174, 94, 12
    for index, (label, center) in enumerate(ART_PATCHES):
        x = x0 + index * (patch_w + gap)
        color = average_patch(source, center)
        draw.rectangle((x, patch_y, x + patch_w - 1, patch_y + patch_h - 1),
                       fill=color, outline=INK, width=2)
        text(draw, (x + patch_w // 2, patch_y + patch_h + 8), label,
             typeface=F_PATCH, fill=INK, anchor="ma")


def draw_neutral_patches(draw: ImageDraw.ImageDraw) -> None:
    x0, y0 = 1880, 2765
    text(draw, (x0, y0), "NEUTRAL sRGB PATCHES (R = G = B)", typeface=F_SMALL_BOLD)
    patch_y = y0 + 53
    patch_w, patch_h, gap = 143, 94, 10
    for index, level in enumerate(NEUTRAL_LEVELS):
        x = x0 + index * (patch_w + gap)
        draw.rectangle((x, patch_y, x + patch_w - 1, patch_y + patch_h - 1),
                       fill=(level, level, level), outline=INK, width=2)
        label_fill = INK
        text(draw, (x + patch_w // 2, patch_y + patch_h + 8), str(level),
             typeface=F_MONO, fill=label_fill, anchor="ma")


def draw_footer(draw: ImageDraw.ImageDraw) -> None:
    x0, y0, x1, y1 = 100, 3035, 3500, 3490
    draw.rounded_rectangle((x0, y0, x1, y1), radius=18, fill=(237, 239, 238),
                           outline=RULE, width=2)
    col1_x, col2_x = 145, 1835
    title_y = y0 + 34
    body_y = title_y + 53
    text(draw, (col1_x, title_y), "WHAT TO COMPARE", typeface=F_SMALL_BOLD, fill=ACCENT)
    draw_wrapped(
        draw,
        (col1_x, body_y),
        "Compare the complete composition for overall color and tone. Compare every detail tile with the same region on the 30-inch print: fine-vein retention, fracture edges, texture, gradients, and subtle center contrast.",
        typeface=F_SMALL,
        max_width=1510,
        line_height=43,
    )
    draw.line((145, 3305, 3455, 3305), fill=RULE, width=2)
    text(draw, (145, 3330), "PRINT RECORD", typeface=F_SMALL_BOLD, fill=ACCENT)
    text(draw, (145, 3396), "Paper / SKU", typeface=F_SMALL, fill=MUTED)
    draw.line((355, 3428, 1535, 3428), fill=RULE, width=2)
    text(draw, (1650, 3396), "Viewing light", typeface=F_SMALL, fill=MUTED)
    draw.line((1900, 3428, 2740, 3428), fill=RULE, width=2)
    text(draw, (2850, 3396), "Date", typeface=F_SMALL, fill=MUTED)
    draw.line((2940, 3428, 3455, 3428), fill=RULE, width=2)
    text(draw, (col2_x, title_y), "PHYSICAL + TRIM CHECK", typeface=F_SMALL_BOLD, fill=ACCENT)
    draw_wrapped(
        draw,
        (col2_x, body_y),
        "Detail scale is matched exactly: 10 master pixels become 9 proof pixels. Under neutral light, compare artwork swatches and neutral steps for casts or blocked tones. The thin outer frame is positioned 3 mm inside every document edge; measure from each cut edge to check trim consistency.",
        typeface=F_SMALL,
        max_width=1575,
        line_height=43,
    )


def build_sheet(source: Image.Image) -> Image.Image:
    canvas = Image.new("RGB", (CANVAS_PX, CANVAS_PX), BACKGROUND)
    draw = ImageDraw.Draw(canvas)

    # At 300 ppi, 3 mm is 35.433 px; 35 px is the nearest addressable raster
    # centerline. The exact value is retained in output metadata.
    frame_box = (
        TRIM_INSET_PX,
        TRIM_INSET_PX,
        CANVAS_PX - 1 - TRIM_INSET_PX,
        CANVAS_PX - 1 - TRIM_INSET_PX,
    )
    draw.rectangle(frame_box, outline=(20, 20, 20), width=1)

    text(draw, (100, 78), "BILLION — 12-INCH PRINT TEST SHEET", typeface=F_TITLE)
    text(
        draw,
        (102, 174),
        "12 × 12 in  •  300 ppi  •  sRGB  •  source: billion_shufflesnap_31623_seed0_test_interp75",
        typeface=F_SUBTITLE,
        fill=MUTED,
    )
    text(
        draw,
        (102, 228),
        "Complete composition + physical-scale detail crops for comparison with a 30-inch print",
        typeface=F_SUBTITLE,
        fill=MUTED,
    )
    draw.line((100, 304, 3500, 304), fill=RULE, width=2)

    # Complete composition.
    overview_xy = (100, 360)
    overview_size = (1700, 1700)
    overview = source.resize(overview_size, Image.Resampling.LANCZOS)
    draw.rectangle((98, 358, 1801, 2061), fill=PANEL, outline=RULE, width=2)
    canvas.paste(overview, overview_xy)
    text(draw, (100, 2078), "COMPLETE COMPOSITION", typeface=F_LABEL)
    text(draw, (100, 2117), "10,000×10,000 px → 1,700×1,700 px (Lanczos)",
         typeface=F_MONO, fill=MUTED)

    # Four square physical-scale details.
    square_positions = ((2080, 360), (2790, 360), (2080, 1125), (2790, 1125))
    square_label_ys = (1017, 1017, 1782, 1782)
    for spec, xy, label_y in zip(SQUARE_CROPS, square_positions, square_label_ys):
        tile, box = crop_at_physical_scale(source, spec.center, (648, 648))
        paste_tile(canvas, draw, tile, xy, spec.label, box=box, label_y=label_y)

    # Clarify physical scale beneath the square crop block.
    draw.rounded_rectangle((2080, 1870, 3438, 2130), radius=14, fill=PANEL,
                           outline=RULE, width=2)
    text(draw, (2115, 1900), "MATCHED DETAIL SCALE", typeface=F_SMALL_BOLD, fill=ACCENT)
    draw_wrapped(
        draw,
        (2115, 1948),
        "The 10,000 px master represents 30 in at 333.33 ppi. Each 720 px source crop is printed as 648 px at 300 ppi, so both represent 2.16 in physically.",
        typeface=F_SMALL,
        max_width=1265,
        line_height=39,
    )

    # Two wide physical-scale details.
    wide_positions = ((100, 2200), (1880, 2200))
    for spec, xy in zip(WIDE_CROPS, wide_positions):
        tile, box = crop_at_physical_scale(source, spec.center, (1620, 432))
        paste_tile(canvas, draw, tile, xy, spec.label, box=box, label_y=2648)

    draw_art_patches(canvas, draw, source)
    draw_neutral_patches(draw)
    draw_footer(draw)
    return canvas


def verify_source(source: Image.Image) -> None:
    if source.size != (10_000, 10_000):
        raise ValueError(f"Source must be exactly 10000×10000; got {source.size}")
    if source.mode != "RGB":
        raise ValueError(f"Source must be RGB; got {source.mode}")


def verify_profile(icc_bytes: bytes) -> None:
    profile = ImageCms.ImageCmsProfile(str(ICC_PROFILE))
    description = ImageCms.getProfileDescription(profile).strip().lower()
    colorspace = profile.profile.xcolor_space.strip().upper()
    if "srgb" not in description or colorspace != "RGB":
        raise ValueError(
            f"ICC profile is not recognized as sRGB RGB: description={description!r}, "
            f"colorspace={colorspace!r}"
        )
    if not icc_bytes:
        raise ValueError("ICC profile is empty")


def save_outputs(sheet: Image.Image, icc_bytes: bytes) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    pnginfo = PngImagePlugin.PngInfo()
    pnginfo.add_text("Title", "Billion 12x12 print test sheet at 300 ppi")
    pnginfo.add_text("SourceColorSpace", "sRGB (renderer-defined; source PNG untagged)")
    pnginfo.add_text("TrimCheckInset", f"{TRIM_INSET_MM:.1f} mm ({TRIM_INSET_PX_EXACT:.6f} px at 300 ppi)")
    pnginfo.add_text("DetailScale", "10 source pixels to 9 proof pixels")
    sheet.save(
        PNG_OUTPUT,
        format="PNG",
        dpi=(PPI, PPI),
        icc_profile=icc_bytes,
        pnginfo=pnginfo,
        compress_level=9,
    )
    sheet.save(
        JPEG_OUTPUT,
        format="JPEG",
        quality=95,
        subsampling=0,
        dpi=(PPI, PPI),
        icc_profile=icc_bytes,
        optimize=False,
        progressive=False,
    )


def inspect_output(path: Path) -> str:
    with Image.open(path) as image:
        embedded = image.info.get("icc_profile")
        if image.size != (CANVAS_PX, CANVAS_PX):
            raise ValueError(f"{path}: wrong dimensions {image.size}")
        if image.mode != "RGB":
            raise ValueError(f"{path}: wrong mode {image.mode}")
        dpi = image.info.get("dpi")
        if not dpi or any(abs(float(v) - PPI) > 0.1 for v in dpi[:2]):
            raise ValueError(f"{path}: wrong or absent DPI {dpi}")
        if not embedded:
            raise ValueError(f"{path}: no embedded ICC profile")
        profile = ImageCms.ImageCmsProfile(__import__("io").BytesIO(embedded))
        description = ImageCms.getProfileDescription(profile).strip()
        colorspace = profile.profile.xcolor_space.strip()
        if "srgb" not in description.lower() or colorspace.upper() != "RGB":
            raise ValueError(f"{path}: embedded profile is not sRGB RGB")
        return (
            f"{path.name}: {image.width}×{image.height}, {image.mode}, "
            f"dpi={dpi[0]:.3f}×{dpi[1]:.3f}, ICC={description!r}"
        )


def main() -> None:
    Image.MAX_IMAGE_PIXELS = None
    if not SOURCE.is_file():
        raise FileNotFoundError(f"Source not found: {SOURCE}")
    if not ICC_PROFILE.is_file():
        raise FileNotFoundError(f"Standard sRGB profile not found: {ICC_PROFILE}")

    icc_bytes = ICC_PROFILE.read_bytes()
    verify_profile(icc_bytes)
    with Image.open(SOURCE) as opened:
        opened.load()
        verify_source(opened)
        source = opened.copy()
    sheet = build_sheet(source)
    save_outputs(sheet, icc_bytes)
    print(f"source: {SOURCE.name}: {source.width}×{source.height}, {source.mode}, intended sRGB")
    print(inspect_output(PNG_OUTPUT))
    print(inspect_output(JPEG_OUTPUT))


if __name__ == "__main__":
    main()
