#!/usr/bin/env python3
"""Center the selected ShuffleSnap render on a print-ready square white canvas."""

from __future__ import annotations

import argparse
import io
from pathlib import Path

from PIL import Image, ImageChops, ImageCms, PngImagePlugin

from prodigi_20x20_config import Paper20x20, get_paper, paper_keys


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = (
    PROJECT_DIR.parent
    / "shufflesnap-paper"
    / "benchmarks/results/renders"
    / "billion_shufflesnap_31623_seed0_interp75_aa_4800"
    / "12_disk_4x_19200_1px_points_box_area_4800.png"
)
PPI = 300
CANVAS_INCHES = 20
CANVAS_PIXELS = PPI * CANVAS_INCHES
EXPECTED_ARTWORK_PIXELS = 4_800
BACKGROUND = (255, 255, 255)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Center a 4800px ShuffleSnap render on a 6000px white sRGB canvas."
    )
    parser.add_argument(
        "--paper",
        choices=paper_keys(),
        default="hpr",
        help="Configured Prodigi paper variant (default: hpr)",
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument(
        "--output",
        type=Path,
        help="Override the configured paper-specific output path",
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def verify_srgb_profile(icc: bytes) -> None:
    profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
    description = ImageCms.getProfileDescription(profile).strip().lower()
    if "srgb" not in description:
        raise ValueError(f"Source ICC profile is not identified as sRGB: {description!r}")


def load_source(path: Path) -> tuple[Image.Image, bytes]:
    if not path.is_file():
        raise FileNotFoundError(path)
    Image.MAX_IMAGE_PIXELS = None
    with Image.open(path) as opened:
        opened.load()
        if opened.size != (EXPECTED_ARTWORK_PIXELS, EXPECTED_ARTWORK_PIXELS):
            raise ValueError(f"Expected 4800×4800 source; found {opened.size}")
        if opened.mode != "RGB":
            raise ValueError(f"Expected RGB source; found {opened.mode}")
        icc = opened.info.get("icc_profile")
        if not isinstance(icc, bytes) or not icc:
            raise ValueError("Source does not contain an embedded ICC profile")
        verify_srgb_profile(icc)
        dpi = opened.info.get("dpi")
        if not dpi or any(abs(float(value) - PPI) > 0.1 for value in dpi[:2]):
            raise ValueError(f"Source does not report 300 ppi: {dpi}")
        return opened.copy(), icc


def build_canvas(source: Image.Image) -> tuple[Image.Image, tuple[int, int]]:
    canvas = Image.new("RGB", (CANVAS_PIXELS, CANVAS_PIXELS), BACKGROUND)
    offset = (
        (CANVAS_PIXELS - source.width) // 2,
        (CANVAS_PIXELS - source.height) // 2,
    )
    if offset != (600, 600):
        raise ValueError(f"Unexpected centering offset: {offset}")
    canvas.paste(source, offset)
    return canvas, offset


def verify_pixels(canvas: Image.Image, source: Image.Image,
                  offset: tuple[int, int]) -> None:
    left, top = offset
    right = left + source.width
    bottom = top + source.height
    centered = canvas.crop((left, top, right, bottom))
    if ImageChops.difference(centered, source).getbbox() is not None:
        raise ValueError("Centered artwork pixels differ from the source")
    border_regions = (
        (0, 0, CANVAS_PIXELS, top),
        (0, bottom, CANVAS_PIXELS, CANVAS_PIXELS),
        (0, top, left, bottom),
        (right, top, CANVAS_PIXELS, bottom),
    )
    for box in border_regions:
        region = canvas.crop(box)
        expected = Image.new("RGB", region.size, BACKGROUND)
        if ImageChops.difference(region, expected).getbbox() is not None:
            raise ValueError(f"Canvas margin is not pure white in region {box}")


def save_output(canvas: Image.Image, output: Path, source: Path,
                icc: bytes, paper: Paper20x20, overwrite: bool) -> None:
    output = output.resolve()
    if output.exists() and not overwrite:
        raise FileExistsError(f"Output exists; use --overwrite: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    if temporary.exists():
        raise FileExistsError(f"Incomplete temporary output exists: {temporary}")
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Title", f"Billion ShuffleSnap — 20×20 inch {paper.paper} print")
    metadata.add_text("SourceArtwork", source.name)
    metadata.add_text("ProdigiSKU", paper.sku)
    metadata.add_text("PaperType", paper.paper)
    metadata.add_text("AssetPurpose", "Prodigi 20-inch paper comparison")
    metadata.add_text("Canvas", "20×20 inches at 300 ppi")
    metadata.add_text("ArtworkPlacement", "4800×4800 pixels centered at x=600, y=600")
    canvas.save(
        temporary,
        format="PNG",
        dpi=(PPI, PPI),
        icc_profile=icc,
        pnginfo=metadata,
        compress_level=9,
        optimize=False,
    )
    temporary.replace(output)


def verify_output(path: Path, source: Image.Image, icc: bytes,
                  paper: Paper20x20) -> None:
    Image.MAX_IMAGE_PIXELS = None
    with Image.open(path) as opened:
        opened.load()
        if opened.size != (CANVAS_PIXELS, CANVAS_PIXELS) or opened.mode != "RGB":
            raise ValueError(f"Invalid output geometry/mode: {opened.size} {opened.mode}")
        if opened.info.get("icc_profile") != icc:
            raise ValueError("Output ICC profile differs from the source sRGB profile")
        if opened.text.get("ProdigiSKU") != paper.sku:
            raise ValueError("Output PNG metadata does not match the selected SKU")
        if opened.text.get("PaperType") != paper.paper:
            raise ValueError("Output PNG metadata does not match the selected paper")
        dpi = opened.info.get("dpi")
        if not dpi or any(abs(float(value) - PPI) > 0.1 for value in dpi[:2]):
            raise ValueError(f"Output does not report 300 ppi: {dpi}")
        verify_pixels(opened, source, (600, 600))


def main() -> int:
    args = parse_args()
    paper = get_paper(args.paper)
    source_path = args.source.expanduser().resolve()
    configured_output = PROJECT_DIR / "outputs/prodigi" / paper.filename
    output_path = (args.output or configured_output).expanduser().resolve()
    source, icc = load_source(source_path)
    canvas, offset = build_canvas(source)
    verify_pixels(canvas, source, offset)
    save_output(canvas, output_path, source_path, icc, paper, args.overwrite)
    verify_output(output_path, source, icc, paper)
    source.close()
    canvas.close()
    print(
        f"WROTE {paper.sku} {output_path}: 6000×6000 RGB, 300 ppi, embedded sRGB, "
        "4800×4800 source unchanged at (600, 600)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
