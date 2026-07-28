# Dev Max — app icon (Cache stack)

Original mark from turn 1 of the icon board: three offset rounded bars, top layer warm.
Art is optically centered (the board version leaned 3px right at 132px); geometry and
colors are otherwise unchanged.

## Geometry (1024 x 1024 master)
| | value |
|---|---|
| bar size | 574 x 155, corner radius 31 |
| bar 1 | x 179, y 233, fill `#262c31` |
| bar 2 | x 225, y 435, fill `#39434a` |
| bar 3 | x 272, y 636, fill `#57b6c2` (accent) |
| background | `#0f1214` |

Scale linearly for other sizes (multiply every value by size/1024). No hairline border —
the 1px `#1c2124` edge in the exploration board was a preview affordance, not part of the mark.

## Files
- `svg/devmax-icon-master.svg` — square full-bleed dark master. Use this for iOS (the OS
  applies the squircle mask) and as the source of truth for any re-export.
- `svg/devmax-icon-light.svg` — light-mode / light-background pair.
- `svg/devmax-icon-monochrome.svg` — bars only in `currentColor` with 35/60/100% opacity.
  For iOS tinted icons, notification icons, and monochrome contexts.
- `svg/devmax-adaptive-background.svg` + `svg/devmax-adaptive-foreground.svg` — Android
  adaptive icon layers (art inset to 66% so nothing is clipped by the system mask).
- `svg/devmax-favicon.svg` — pre-rounded 64px favicon.
- `png/devmax-icon-{1024,512,180,167,152,120,87,80,60,40}.png` — square dark raster set
  (iOS App Store 1024, Play Store 512, plus the standard iOS device sizes).
- `png/light/` — light pair at 1024/180/120/60.
- `png/android/devmax-adaptive-foreground-{512,192,144,96}.png` — transparent foreground layers.
- `png/devmax-icon-rounded-{256,64}.png` — pre-masked squircle (radius 230/1024) for
  desktop, web manifests, and anywhere the platform does not round for you.

## Notes
- Never pre-round for iOS — ship the square master.
- Notification badge sits top-right and clears the stack; no layout change needed.
- Accent `#57b6c2` matches the in-app accent, so the icon, splash mark, and UI stay in sync.
