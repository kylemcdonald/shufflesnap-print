#!/usr/bin/env python3
"""Create deterministic, SKU-labelled Prodigi proof assets from the base sheet."""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageCms, ImageDraw, ImageFont, PngImagePlugin


PROJECT_DIR = Path(__file__).resolve().parents[1]
BASE_PROOF = PROJECT_DIR / "outputs/billion_12x12_print_test_sheet_300ppi.png"
OUTPUT_DIR = PROJECT_DIR / "outputs/prodigi"
FONT_BOLD = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
FONT_REGULAR = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
FONT_MONO = Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")
PPI = 300


@dataclass(frozen=True)
class PaperVariant:
    sku: str
    paper: str
    filename: str
    accent: tuple[int, int, int]
    pale: tuple[int, int, int]


VARIANTS = (
    PaperVariant(
        sku="GLOBAL-HPR-12X12",
        paper="Hahnemühle Photo Rag",
        filename="billion_12x12_global-hpr-12x12_300ppi.png",
        accent=(0, 112, 126),
        pale=(225, 243, 243),
    ),
    PaperVariant(
        sku="GLOBAL-FAP-12X12",
        paper="Enhanced Matte Art",
        filename="billion_12x12_global-fap-12x12_300ppi.png",
        accent=(105, 75, 154),
        pale=(239, 233, 248),
    ),
    PaperVariant(
        sku="ART-FAP-BAP-12X12",
        paper="Budget Art Paper",
        filename="billion_12x12_art-fap-bap-12x12_300ppi.png",
        accent=(179, 83, 20),
        pale=(251, 236, 221),
    ),
    PaperVariant(
        sku="ART-FAP-SAP-12X12",
        paper="Smooth Art Paper",
        filename="billion_12x12_art-fap-sap-12x12_300ppi.png",
        accent=(42, 116, 72),
        pale=(230, 244, 234),
    ),
)


def load_font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    if not path.is_file():
        raise FileNotFoundError(path)
    return ImageFont.truetype(str(path), size=size)


F_BADGE_LABEL = load_font(FONT_BOLD, 27)
F_BADGE_PAPER = load_font(FONT_BOLD, 39)
F_BADGE_SKU = load_font(FONT_MONO, 27)
F_RECORD = load_font(FONT_REGULAR, 26)


def verify_base(base: Image.Image) -> tuple[bytes, tuple[float, float]]:
    if base.size != (3600, 3600) or base.mode != "RGB":
        raise ValueError(f"Unexpected base proof geometry/mode: {base.size} {base.mode}")
    icc = base.info.get("icc_profile")
    if not icc:
        raise ValueError("Base proof has no embedded ICC profile")
    profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
    if "srgb" not in ImageCms.getProfileDescription(profile).strip().lower():
        raise ValueError("Base proof ICC profile is not sRGB")
    dpi = base.info.get("dpi")
    if not dpi or any(abs(float(value) - PPI) > 0.1 for value in dpi[:2]):
        raise ValueError(f"Base proof is not 300 ppi: {dpi}")
    return icc, (float(dpi[0]), float(dpi[1]))


def render_variant(base: Image.Image, variant: PaperVariant) -> Image.Image:
    image = base.copy()
    draw = ImageDraw.Draw(image)

    # Product badge in the intentionally unused right side of the header.
    badge = (2380, 72, 3500, 265)
    draw.rounded_rectangle(badge, radius=18, fill=variant.pale, outline=variant.accent, width=3)
    draw.rectangle((2410, 102, 2420, 230), fill=variant.accent)
    draw.text((2450, 94), "PAPER VARIANT", font=F_BADGE_LABEL, fill=variant.accent)
    draw.text((2450, 132), variant.paper, font=F_BADGE_PAPER, fill=(29, 31, 34))
    draw.text((2450, 190), variant.sku, font=F_BADGE_SKU, fill=(70, 74, 78))

    # Fill the existing paper/SKU field while preserving the rest of the sheet.
    draw.rectangle((355, 3387, 371, 3418), fill=variant.accent)
    draw.text(
        (388, 3387),
        f"{variant.paper}  •  {variant.sku}",
        font=F_RECORD,
        fill=(47, 50, 53),
    )
    return image


def verify_changes_only_in_labels(base: Image.Image, rendered: Image.Image) -> None:
    diff = ImageChops.difference(base, rendered).convert("L")
    allowed = Image.new("L", base.size, 0)
    allowed_draw = ImageDraw.Draw(allowed)
    allowed_draw.rectangle((2375, 67, 3505, 270), fill=255)
    allowed_draw.rectangle((350, 3380, 1540, 3425), fill=255)
    outside = ImageChops.multiply(diff, ImageChops.invert(allowed))
    if outside.getbbox() is not None:
        raise ValueError("Variant changed pixels outside the two label regions")


def save_variant(rendered: Image.Image, variant: PaperVariant, icc: bytes) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output = OUTPUT_DIR / variant.filename
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Title", f"Billion 12x12 print test — {variant.paper}")
    metadata.add_text("ProdigiSKU", variant.sku)
    metadata.add_text("PaperType", variant.paper)
    metadata.add_text("AssetPurpose", "Prodigi sandbox 12-inch paper comparison")
    rendered.save(
        output,
        format="PNG",
        dpi=(PPI, PPI),
        icc_profile=icc,
        pnginfo=metadata,
        compress_level=9,
    )
    return output


def verify_output(path: Path, variant: PaperVariant) -> None:
    with Image.open(path) as image:
        if image.size != (3600, 3600) or image.mode != "RGB":
            raise ValueError(f"{path.name}: invalid geometry/mode")
        if image.text.get("ProdigiSKU") != variant.sku:
            raise ValueError(f"{path.name}: missing SKU metadata")
        dpi = image.info.get("dpi")
        if not dpi or any(abs(float(value) - PPI) > 0.1 for value in dpi[:2]):
            raise ValueError(f"{path.name}: invalid DPI {dpi}")
        embedded = image.info.get("icc_profile")
        if not embedded:
            raise ValueError(f"{path.name}: missing ICC profile")
        profile = ImageCms.ImageCmsProfile(io.BytesIO(embedded))
        if "srgb" not in ImageCms.getProfileDescription(profile).strip().lower():
            raise ValueError(f"{path.name}: ICC profile is not sRGB")


def main() -> None:
    Image.MAX_IMAGE_PIXELS = None
    with Image.open(BASE_PROOF) as opened:
        opened.load()
        icc, _dpi = verify_base(opened)
        base = opened.copy()
    for variant in VARIANTS:
        rendered = render_variant(base, variant)
        verify_changes_only_in_labels(base, rendered)
        output = save_variant(rendered, variant, icc)
        verify_output(output, variant)
        print(f"{variant.sku}: {output.name}: 3600×3600 RGB 300 ppi sRGB")


if __name__ == "__main__":
    main()
