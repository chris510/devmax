# Unprompted — Balanced identity kit

**Selected by the user on September 9, 2026.** The working name is **Unprompted**; **Openloop** remains a possible future name. The selected symbol is **01 / Balanced**.

This kit translates the approved raster concept into exact, reproducible vector artwork. All wordmark letters are outlines from the bundled **IBM Plex Sans, weight 500, width 100**, shaped with HarfBuzz so kerning is preserved. The actual **Newsreader** font supplies the editorial type in the proof.

## Start here

- [Proof sheet](png/proof-sheet-1200.png): final geometry, real typography, and icon PNGs shown at their native pixel dimensions.
- [Dark app icon — 1024px, RGB](png/icon-dark-1024.png): square, opaque application source.
- [Paper app icon — 1024px, RGB](png/icon-paper-1024.png): alternate brand colorway.
- [Dark foreground lockup](svg/lockup-dark.svg): for paper/light backgrounds.
- [Light foreground lockup](svg/lockup-light.svg): for charcoal/dark backgrounds.
- [Monochrome lockup](svg/lockup-monochrome.svg): follows `currentColor` when inlined.
- [Standalone geometry master](svg/mark.svg).
- [Small-size checks](small-size-checks.json) and [export inventory](exports.json).

## Geometry

The single authored geometry source is `svg/mark.svg`. Generated variants must be regenerated rather than hand-edited.

| Property | Master units |
| --- | --- |
| Canvas | 1024 × 1024 |
| Horizontal symbol bounds | x = 204…820 (616 wide) |
| Main band width | 140 |
| Outer bowl radius | 308 |
| Inner bowl radius | 168 |
| Main terminal corner radius | 24 |
| Detached square | x = 680, y = 244, width = height = 140 |
| Detached square corner radius | 24 |
| Normal vertical gap | 48 |
| 16px optical correction | Square moved up 48 units; gap becomes 96 |

The **16px** PNGs use the derived `icon-*-micro.svg` masters. Direct reduction of the ordinary master to 16px closes the gap through antialiasing. Sizes **24px and above** use the ordinary geometry. The small-size check verifies two separate visible components in both colorways at 16, 24, 32, 40, 60, 80, and 120px.

The check thresholds antialiased pixels at a maximum channel difference of 48 from the background, then counts connected components with four-neighbor adjacency. It catches the observed 16px merge; it does not replace visual inspection.

## Files and usage

- `svg/mark.svg`: near-white curve and cyan square on transparency, with the full icon canvas.
- `svg/mark-ink.svg`: charcoal curve and cyan square on transparency.
- `svg/mark-monochrome.svg`: both pieces in `currentColor`. For SVG loaded through an image element, set the color inside the SVG or choose an explicit color export; host CSS does not flow into external image resources.
- `svg/icon-dark.svg`, `svg/icon-paper.svg`: full-bleed square icon masters.
- `svg/icon-*-micro.svg`: 16px favicon variants only.
- `svg/icon-*-rounded.svg`: rounded previews with transparent corners. The 230-unit radius is a preview treatment, not an exact operating-system mask.
- `svg/lockup-*.svg`: symbol plus outlined title-case name. Dark/light refer to **foreground** color.
- `svg/wordmark-*.svg`: outlined name without the symbol.
- `png/icon-{dark,paper}-{size}.png`: RGB exports at 16, 24, 32, 40, 60, 80, 120, 180, 512, and 1024px.
- `png/icon-*-rounded-{64,256}.png`: RGBA previews, preserving real corner transparency.
- `png/lockup-*.png`: transparent raster lockups at 2×.
- `svg/proof-sheet.svg`: self-contained proof with outlined typography and embedded actual PNG exports.
- `OFL-IBMPlex.txt`: the bundled font's license.

Use the **square RGB 1024px dark export** for the iOS asset catalog; the operating system supplies its mask. Keep transparent mark and rounded-preview files separate from application icon submissions.

## Visual system

| Role | Value |
| --- | --- |
| Charcoal | `#0F1214` |
| Paper | `#F4F1EA` |
| Near-white | `#F2F4F5` |
| Cyan detail | `#57B6C2` |
| Wordmark | IBM Plex Sans Medium |
| Editorial voice | Newsreader Regular |
| Metadata | Existing IBM Plex Mono, sparingly |

Keep cyan concentrated in the detached square. A one-color mark must preserve both pieces. Keep the symbol upright and maintain its proportions. For a bare symbol, reserve at least one band width of clear space; the full-canvas mark already includes that space. Do not reconstruct the mark with a font character or use the square as a separate decorative motif.

The paper icon is an alternate brand treatment, not a light-mode app design. The existing dark-only UI policy still applies.

The line **“Make what you learn your own.”** remains a positioning study shown in the proof, not a replacement for approved in-app copy.

## Reproduction

From the repository root:

```sh
uv run scripts/export_brand.py
```

The script declares pinned Python dependencies and uses the bundled font files. It also requires `rsvg-convert` (librsvg); this kit was exported using librsvg 2.62.3. Every rerun generates the colorways, outlines, PNGs, inventory, native-size proof, and separation checks. It rejects incorrectly sized or alpha-bearing application icons, merged small-size geometry, and rounded previews with opaque corners.

## iOS and web integration

The selected kit is installed in the iOS asset catalog and the website. The dark
1024px RGB icon supplies `ios/Devmax/Assets.xcassets/AppIcon.appiconset/icon-1024.png`.
The site uses outlined light/dark lockups in its header, footer, and privacy page,
the standalone mark on narrow headers, explicit 16px/32px favicons, and a 180px
Apple touch icon. `web/public/unprompted-icon.png` is also refreshed for existing
links. The site retains its established page copy and social-preview image.

After re-exporting the artwork, install it with:

```sh
python3 scripts/sync_brand.py
python3 scripts/sync_brand.py --check
python3 scripts/check_icon_alpha.py
```

`sync_brand.py` copies the selected exports without resampling. CI verifies that
all eight integrated files exactly match the kit and separately enforces the
iOS marketing icon's RGB/no-alpha requirement. Application resources are updated
by this explicit sync step, not by `export_brand.py` alone.
