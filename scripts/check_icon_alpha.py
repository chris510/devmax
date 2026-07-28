#!/usr/bin/env python3
"""Enforce the app-icon alpha rules from docs/DEVIATIONS.md §Icon.

App Store Connect rejects a marketing icon that carries an alpha channel, and it
rejects it at *upload* — an RGBA icon builds, installs, and runs fine, so nothing
in the normal loop catches the regression. Re-exporting the kit is what breaks
it: the kit masters ship RGBA, and the catalog copy is the one file re-encoded
without alpha.

The inverse mistake is just as easy. Stripping alpha across the whole kit
destroys the four Android adaptive foregrounds and the two rounded icons, whose
transparency is real.

So every icon PNG in the repo must land in exactly one of three buckets, and a
file matching none of them is itself a failure — otherwise a newly exported size
is covered by no rule and CI stays green while saying nothing about it.

Reads the PNG header directly — colortype is byte 25, inside the IHDR chunk that
the spec requires to come first. No dependency, no decode.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
COLORTYPE_RGB = 2
COLORTYPES_WITH_ALPHA = (4, 6)  # grayscale+alpha, RGBA

KIT = Path("assets/app_icon/png")
APPICONSET = Path("ios/Devmax/Assets.xcassets/AppIcon.appiconset")

# Transparency here is load-bearing: the Android foregrounds are composited over
# a separate background layer, and the rounded icons are masked art. This stays
# an explicit list rather than a glob — "this file's transparency is intended" is
# a fact about the art, not something the filename structurally guarantees, and a
# glob that stops matching would pass vacuously instead of failing loudly.
MUST_KEEP_ALPHA = {
    KIT / "android/devmax-adaptive-foreground-96.png",
    KIT / "android/devmax-adaptive-foreground-144.png",
    KIT / "android/devmax-adaptive-foreground-192.png",
    KIT / "android/devmax-adaptive-foreground-512.png",
    KIT / "devmax-icon-rounded-64.png",
    KIT / "devmax-icon-rounded-256.png",
}

# The 14 full-bleed squares. Their alpha is uniformly 255 — dead weight, but
# harmless, because nothing ships them directly. Only the copy that reaches the
# asset catalog has to be stripped, so these are deliberately unconstrained.
FULL_BLEED = (1024, 512, 180, 167, 152, 120, 87, 80, 60, 40)
UNCONSTRAINED = {KIT / f"devmax-icon-{size}.png" for size in FULL_BLEED} | {
    KIT / f"light/devmax-icon-light-{size}.png" for size in (1024, 180, 120, 60)
}


def colortype(path: Path) -> int:
    header = path.read_bytes()[:26]
    if header[:8] != PNG_MAGIC:
        raise ValueError(f"{path}: not a PNG")
    if header[12:16] != b"IHDR":
        raise ValueError(f"{path}: first chunk is not IHDR")
    return header[25]


def catalog_icons() -> set[Path]:
    """Whatever the asset catalog declares — derived, not hardcoded.

    `Contents.json` is the authoritative list, and it grows: adding a light or
    tinted appearance slot puts a second marketing-visible PNG in here, and that
    one needs the same treatment. Entries without a `filename` are unassigned
    slots Xcode writes as placeholders.
    """
    contents = json.loads((ROOT / APPICONSET / "Contents.json").read_text())
    return {APPICONSET / img["filename"] for img in contents["images"] if img.get("filename")}


def main() -> int:
    must_be_opaque = catalog_icons()
    known = must_be_opaque | MUST_KEEP_ALPHA | UNCONSTRAINED
    failures: list[str] = []

    for rel in sorted(must_be_opaque | MUST_KEEP_ALPHA):
        path = ROOT / rel
        if not path.exists():
            failures.append(f"{rel}: missing")
            continue
        found = colortype(path)
        if rel in must_be_opaque and found != COLORTYPE_RGB:
            failures.append(
                f"{rel}: colortype {found}, expected {COLORTYPE_RGB} (RGB, no alpha). "
                "App Store Connect rejects a marketing icon with an alpha channel. "
                "Re-encode it — see docs/DEVIATIONS.md §Icon."
            )
        elif rel in MUST_KEEP_ALPHA and found not in COLORTYPES_WITH_ALPHA:
            failures.append(
                f"{rel}: colortype {found} has no alpha channel. This file's "
                "transparency is real — flattening it destroys the art. Alpha was "
                "probably stripped across the whole kit instead of just the copy "
                "that reaches the asset catalog."
            )

    # A file no rule mentions is a gap in this script, not a passing file.
    for path in sorted((ROOT / KIT).rglob("*.png")) + sorted((ROOT / APPICONSET).glob("*.png")):
        rel = path.relative_to(ROOT)
        if rel not in known:
            failures.append(
                f"{rel}: no alpha rule covers this file. Add it to MUST_KEEP_ALPHA if its "
                "transparency is intended, or to UNCONSTRAINED if it is a full-bleed square "
                "that never ships directly."
            )

    for line in failures:
        print(f"error: {line}", file=sys.stderr)
    if failures:
        return 1
    print(f"icon alpha rules ok ({len(known)} files covered)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
