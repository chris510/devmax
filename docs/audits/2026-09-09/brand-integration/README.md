# Balanced identity integration — September 9, 2026

The approved [Unprompted identity](../../../../assets/brand/unprompted/README.md)
is integrated into the iOS icon and existing website.

## Changes

- iOS: the selected dark 1024px RGB icon replaces the historical Cache icon in
  the asset catalog. The existing Unprompted display name remains.
- Web: a shared component renders outlined lockups in the product header,
  footer, and privacy page. The narrow product header uses the standalone mark.
  Explicit 16px/32px favicons and a 180px Apple touch icon accompany the updated
  existing 512px icon URL.
- `scripts/sync_brand.py` installs eight selected exports without resampling;
  CI runs its `--check` mode alongside the existing icon-alpha check.
- The initial in-app design, page copy, and existing social-preview image are
  unchanged by this integration.

## Verification

- `python3 scripts/sync_brand.py --check`: all eight resources match the kit.
- `python3 scripts/check_icon_alpha.py`: all 21 covered files pass.
- Ruff lint and format checks pass for the sync script.
- `npm test` in `web/`: production build succeeds and both rendered-page tests
  pass.
- Browser checks at the default 1280px width and 390×844 confirm that the
  visible brand images load, the mobile header switches correctly, and the
  header, footer, and privacy page have no horizontal overflow.
- Xcode 26.6 Debug simulator build succeeds; the app installs and launches on
  an iPhone 16e with iOS 26.3.1 at 390×844 points. The home-screen icon visually
  matches the selected master. The ordinary question screen still renders.

The installed simulator runtime is older than the Xcode SDK. Building required
a temporary `simctl runtime match` override from the iPhoneOS 26.5 SDK to runtime
build `23D8133`. The override was removed after the build; `runtime match list`
confirms no user override remains. This verifies the simulator build and icon,
not App Store or TestFlight distribution.

## Screenshots

- [Installed iOS home-screen icon](ios-home.png)
- [iOS question screen](ios-question.png)
- [Desktop website](web-desktop.png)
- [Mobile website](web-mobile.png)
- [Mobile footer](web-footer-mobile.png)
- [Mobile privacy page](web-privacy-mobile.png)

## Publication handoff

The exact validated web source was copied into a separate checkout of the
existing Sites repository, leaving the parent project uncommitted. Every one
of its 34 source files was compared byte-for-byte before the validated build
output was copied and packaged. The archive's seven website brand files were
also checked against their sources.

- Sites source commit: `541406c016f9e6ab4e09cfb27796cbfc3a9dd4b2`
- Saved version: **6**
- Version ID: `appgprj_6a76496174cc8191a03545a948c97515~appgver_8d1b00b8931481919db00705f122300d`
- Site: `appgprj_6a76496174cc8191a03545a948c97515`
- Existing public URL: <https://devmax-recall.christrinh5.chatgpt.site>

Version 6 was published to the existing public audience after the user's
approval on September 9, 2026. Sites reports `succeeded` for deployment
`appgdep_6aa1f5b7cd9481918ce81315a61e799e` at 2026-09-10 00:11:46 UTC
(September 9, 17:11 Pacific). The public URL above serves this release.
No iOS distribution was performed; that remains a separate release step.
