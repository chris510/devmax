#!/usr/bin/env python3
"""Install the selected brand exports, or verify that app resources match them."""

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "assets/brand/unprompted"
COPIES = {
    "png/icon-dark-1024.png": "ios/Devmax/Assets.xcassets/AppIcon.appiconset/icon-1024.png",
    "png/icon-dark-512.png": "web/public/unprompted-icon.png",
    "png/icon-dark-180.png": "web/public/apple-touch-icon.png",
    "png/icon-dark-16.png": "web/public/brand/favicon-16.png",
    "png/icon-dark-32.png": "web/public/brand/favicon-32.png",
    "svg/lockup-light.svg": "web/public/brand/unprompted-lockup-light.svg",
    "svg/lockup-dark.svg": "web/public/brand/unprompted-lockup-dark.svg",
    "svg/mark.svg": "web/public/brand/unprompted-mark-light.svg",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Report stale assets without writing"
    )
    args = parser.parse_args()
    missing = [source for source in COPIES if not (KIT / source).is_file()]
    if missing:
        parser.error(
            f"Missing brand exports: {', '.join(missing)}. Run scripts/export_brand.py first."
        )

    failures = []
    for source, destination in COPIES.items():
        src, dest = KIT / source, ROOT / destination
        if args.check:
            if not dest.is_file() or src.read_bytes() != dest.read_bytes():
                failures.append(destination)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
    if failures:
        for path in failures:
            print(f"Stale brand resource: {path}")
        print(
            "Run python3 scripts/sync_brand.py after exporting the selected identity."
        )
        return 1
    print(
        f"Brand resources {'verified' if args.check else 'installed'} ({len(COPIES)} files)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
