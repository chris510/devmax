#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["fonttools==4.64.0", "uharfbuzz==0.56.1", "pillow==12.3.0"]
# ///
"""Export the approved Unprompted identity. Requires rsvg-convert on PATH.

Run `uv run scripts/export_brand.py` from any directory. mark.svg is the
geometry source; the bundled IBM Plex font supplies the outlined wordmark.
The script writes only assets/brand/unprompted, never application resources.
"""

import argparse
import base64
import json
import shutil
import subprocess
from copy import deepcopy
from functools import lru_cache
from html import escape
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET

import uharfbuzz as hb
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "assets/brand/unprompted"
SVG = KIT / "svg"
PNG = KIT / "png"
FONTS = ROOT / "ios/Devmax/Resources/Fonts"
CHARCOAL, PAPER, WHITE, CYAN = "#0f1214", "#f4f1ea", "#f2f4f5", "#57b6c2"
NS = "{http://www.w3.org/2000/svg}"
ET.register_namespace("", NS[1:-1])


def svg(body: str, width: float, height: float, title: str) -> str:
    return (
        f'<svg xmlns="{NS[1:-1]}" viewBox="0 0 {width:g} {height:g}" '
        f'width="{width:g}" height="{height:g}" role="img">'
        f"<title>{escape(title)}</title>{body}</svg>\n"
    )


def mark(ink: str, point: str, micro=False) -> str:
    """Use the exact two authored shapes for every colorway and lockup."""
    root = ET.parse(SVG / "mark.svg").getroot()
    parts = []
    for element_id, color in (("loop", ink), ("point", point)):
        element = deepcopy(root.find(f".//*[@id='{element_id}']"))
        if element is None:
            raise ValueError(f"Missing {element_id} in mark.svg")
        element.attrib.pop("id")
        element.set("fill", color)
        if micro and element_id == "point":
            # At 16px, the normal 48-unit gap antialiases into the stem.
            # Raise only the square by 48 units to preserve two components.
            element.set("y", str(float(element.get("y")) - 48))
        parts.append(ET.tostring(element, encoding="unicode"))
    return "".join(parts)


@lru_cache
def typeface(filename: str, weight: int, optical_size: float):
    """Share instantiated fonts across the proof sheet's labels."""
    font = TTFont(FONTS / filename)
    axes = {a.axisTag: a.defaultValue for a in font["fvar"].axes}
    axes["wght"] = weight
    if "opsz" in axes:
        axes["opsz"] = optical_size
    font = instantiateVariableFont(font, axes, inplace=True)
    data = BytesIO()
    font.save(data)
    shaping_font = hb.Font(hb.Face(data.getvalue()))
    hb.ot_font_set_funcs(shaping_font)
    upem = font["head"].unitsPerEm
    shaping_font.scale = (upem, upem)
    return font, shaping_font, upem


def outline(text: str, filename: str, size: float, weight: int = 500):
    """Shape with HarfBuzz before outlining, retaining real font kerning."""
    optical_size = min(72, max(6, size)) if filename == "Newsreader.ttf" else 0
    font, shaping_font, upem = typeface(filename, weight, optical_size)
    buffer = hb.Buffer()
    buffer.add_str(text)
    buffer.guess_segment_properties()
    hb.shape(shaping_font, buffer)
    glyphs = font.getGlyphSet()
    glyph_order = font.getGlyphOrder()
    scale, cursor = size / upem, 0
    paths = []
    for info, pos in zip(buffer.glyph_infos, buffer.glyph_positions, strict=True):
        pen = SVGPathPen(glyphs)
        transform = TransformPen(
            pen,
            (
                scale,
                0,
                0,
                -scale,
                (cursor + pos.x_offset) * scale,
                -pos.y_offset * scale,
            ),
        )
        glyphs[glyph_order[info.codepoint]].draw(transform)
        paths.append(pen.getCommands())
        cursor += pos.x_advance
    cap_height = font["OS/2"].sCapHeight * scale
    return f'<path d="{" ".join(paths)}"/>', cursor * scale, cap_height


def label(text: str, x: float, y: float, size: float, color=CHARCOAL, serif=False):
    path, _, _ = outline(
        text, "Newsreader.ttf" if serif else "IBMPlexSans.ttf", size, 400
    )
    return f'<g fill="{color}" transform="translate({x:g} {y:g})">{path}</g>'


def render(
    filename: str, width: int, height: int | None = None, opaque=False, output_name=None
):
    source = SVG / filename
    target = PNG / (output_name or f"{source_name(filename)}-{width}.png")
    args = ["rsvg-convert", "-w", str(width)]
    if height is not None:
        args += ["-h", str(height)]
    result = subprocess.run(args + [str(source)], check=True, capture_output=True)
    with Image.open(BytesIO(result.stdout)) as image:
        image = image.convert("RGB" if opaque else "RGBA")
        image.save(target)
    return target


def source_name(filename: str) -> str:
    return Path(filename).stem


def visible_components(image, background):
    """Check separation on the rendered pixels, including antialiased edges."""
    width, height = image.size
    points = {
        (x, y)
        for y in range(height)
        for x in range(width)
        if max(
            abs(a - b) for a, b in zip(image.getpixel((x, y)), background, strict=True)
        )
        >= 48
    }
    areas = []
    while points:
        stack, area = [points.pop()], 0
        while stack:
            x, y = stack.pop()
            area += 1
            for neighbor in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if neighbor in points:
                    points.remove(neighbor)
                    stack.append(neighbor)
        areas.append(area)
    return sorted(areas)


def main():
    if not shutil.which("rsvg-convert"):
        raise SystemExit("Install librsvg to provide rsvg-convert, then rerun.")
    PNG.mkdir(parents=True, exist_ok=True)
    for name, background, ink in (
        ("dark", CHARCOAL, WHITE),
        ("paper", PAPER, CHARCOAL),
    ):
        body = f'<rect width="1024" height="1024" fill="{background}"/>' + mark(
            ink, CYAN
        )
        (SVG / f"icon-{name}.svg").write_text(
            svg(body, 1024, 1024, f"Unprompted — {name} app icon")
        )
        micro = f'<rect width="1024" height="1024" fill="{background}"/>' + mark(
            ink, CYAN, micro=True
        )
        (SVG / f"icon-{name}-micro.svg").write_text(
            svg(micro, 1024, 1024, f"Unprompted — {name} favicon, optimized for 16px")
        )
        rounded = (
            f'<rect width="1024" height="1024" rx="230" fill="{background}"/>'
            + mark(ink, CYAN)
        )
        (SVG / f"icon-{name}-rounded.svg").write_text(
            svg(rounded, 1024, 1024, f"Unprompted — {name} icon preview")
        )

    (SVG / "mark-monochrome.svg").write_text(
        svg(
            mark("currentColor", "currentColor"),
            1024,
            1024,
            "Unprompted — monochrome mark",
        )
    )
    (SVG / "mark-ink.svg").write_text(
        svg(mark(CHARCOAL, CYAN), 1024, 1024, "Unprompted — mark on light backgrounds")
    )

    word_path, word_width, cap = outline("Unprompted", "IBMPlexSans.ttf", 72)
    mark_height, baseline, inset, gap = cap * 1.12, 96, 24, 24
    mark_scale = mark_height / 540
    text_x = inset + 616 * mark_scale + gap
    lockup_width = round(text_x + word_width + inset)
    for name, ink, point in (
        ("dark", CHARCOAL, CYAN),
        ("light", WHITE, CYAN),
        ("monochrome", "currentColor", "currentColor"),
    ):
        symbol = (
            f'<g transform="translate({inset} {baseline - mark_height:g}) '
            f'scale({mark_scale:g}) translate(-204 -244)">{mark(ink, point)}</g>'
        )
        body = (
            symbol
            + f'<g fill="{ink}" transform="translate({text_x:g} {baseline})">{word_path}</g>'
        )
        (SVG / f"lockup-{name}.svg").write_text(
            svg(body, lockup_width, 132, "Unprompted")
        )
        word_body = f'<g fill="{ink}" transform="translate(12 76)">{word_path}</g>'
        (SVG / f"wordmark-{name}.svg").write_text(
            svg(word_body, round(word_width + 24), 100, "Unprompted")
        )

    sizes = (16, 24, 32, 40, 60, 80, 120, 180, 512, 1024)
    for name in ("dark", "paper"):
        for size in sizes:
            source = f"icon-{name}-micro.svg" if size == 16 else f"icon-{name}.svg"
            render(
                source, size, size, opaque=True, output_name=f"icon-{name}-{size}.png"
            )
        for size in (64, 256):
            render(f"icon-{name}-rounded.svg", size, size)
    for name in ("dark", "light", "monochrome"):
        render(f"lockup-{name}.svg", lockup_width * 2, 264)
    for size in (24, 40, 60, 100, 1024):
        render("mark-monochrome.svg", size, size)
    license_text = (FONTS / "OFL-IBMPlex.txt").read_text()
    (KIT / "OFL-IBMPlex.txt").write_text(
        "\n".join(line.rstrip() for line in license_text.splitlines()) + "\n"
    )

    # The proof sheet embeds the actual exports at 1:1 size: no enlarged labels
    # masquerading as small-size checks, and no external file/font dependencies.
    def embed(name, x, y, width, height):
        data = base64.b64encode((PNG / name).read_bytes()).decode()
        return f'<image x="{x}" y="{y}" width="{width}" height="{height}" href="data:image/png;base64,{data}"/>'

    body = f'<rect width="1200" height="960" fill="{PAPER}"/>'
    body += f'<rect width="1200" height="420" fill="{CHARCOAL}"/>'
    body += label("Unprompted / Balanced", 48, 52, 20, WHITE)
    body += embed(f"lockup-light-{lockup_width * 2}.png", 34, 90, lockup_width, 132)
    body += label("Make what you learn", 48, 282, 52, WHITE, True)
    body += label("your own.", 48, 344, 52, WHITE, True)
    body += embed("icon-paper-rounded-256.png", 864, 82, 256, 256)
    body += label("Actual-size exports", 48, 475, 24)
    body += label("Dark", 48, 532, 17)
    body += label("Paper", 48, 648, 17)
    for size, x in (
        (16, 175),
        (24, 265),
        (32, 365),
        (40, 475),
        (60, 595),
        (80, 735),
        (120, 905),
    ):
        body += embed(f"icon-dark-{size}.png", x, 486 + (120 - size) / 2, size, size)
        body += embed(f"icon-paper-{size}.png", x, 602 + (120 - size) / 2, size, size)
        body += label(f"{size}px", x, 752, 14)
    body += label("One-color silhouette", 48, 815, 20)
    for size, x in ((40, 60), (60, 140), (100, 250)):
        body += embed(f"mark-monochrome-{size}.png", x, 827, size, size)
    body += label("IBM Plex Sans Medium / outlined wordmark", 490, 825, 18)
    body += label("Newsreader / editorial typography", 490, 862, 20, serif=True)
    body += label("Charcoal #0F1214    Paper #F4F1EA    Cyan #57B6C2", 490, 906, 16)
    (SVG / "proof-sheet.svg").write_text(
        svg(body, 1200, 960, "Unprompted — final vector and actual-size export proof")
    )
    render("proof-sheet.svg", 1200, 960, opaque=True)

    report, small_size_checks = [], []
    for path in sorted(PNG.glob("*.png")):
        with Image.open(path) as image:
            image.load()
            if (
                path.stem.startswith(("icon-dark-", "icon-paper-"))
                and "rounded" not in path.stem
            ):
                assert image.mode == "RGB", f"{path}: app icon must not contain alpha"
                expected_size = int(path.stem.rsplit("-", 1)[1])
                assert image.size == (expected_size, expected_size)
                if expected_size <= 120:
                    background = (
                        (15, 18, 20) if "dark" in path.stem else (244, 241, 234)
                    )
                    areas = visible_components(image, background)
                    if len(areas) != 2:
                        raise ValueError(
                            f"{path}: square and curve must remain separate; got {areas}"
                        )
                    small_size_checks.append(
                        {"file": path.name, "visible_components": areas}
                    )
            if "rounded" in path.stem:
                assert image.mode == "RGBA" and image.getpixel((0, 0))[3] == 0
            report.append(
                {
                    "file": str(path.relative_to(KIT)),
                    "size": list(image.size),
                    "mode": image.mode,
                }
            )
    (KIT / "exports.json").write_text(json.dumps(report, indent=2) + "\n")
    (KIT / "small-size-checks.json").write_text(
        json.dumps(small_size_checks, indent=2) + "\n"
    )
    print(
        f"Exported and validated {len(report)} PNGs; vector sources and outlined lockups in {SVG.relative_to(ROOT)}"
    )


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    main()
