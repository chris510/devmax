# Unprompted landing page

The public product landing page for Unprompted, built with
[vinext](https://github.com/cloudflare/vinext) for OpenAI Sites.

## Prerequisites

- Node.js `>=22.13.0`

## Quick Start

```bash
npm install
npm run dev
npm run build
```

This starter does not use `wrangler.jsonc`.

## Useful Commands

- `npm run dev`: start local development
- `npm run build`: verify the vinext build output
- `npm test`: build and verify the server-rendered landing page

## Brand assets

`app/brand.tsx` uses the selected Balanced vector lockups. Header, footer, and
privacy-page branding share that component; narrow product headers show the
standalone mark. Favicons use the kit's 16px optical correction and 32px export,
with a separate 180px Apple touch icon.

The source kit lives at `../assets/brand/unprompted`. After regenerating it, run
`python3 scripts/sync_brand.py` from the repository root. CI's
`python3 scripts/sync_brand.py --check` detects stale web or iOS copies.

## Learn More

- [vinext Documentation](https://github.com/cloudflare/vinext)
- [Drizzle D1 Guide](https://orm.drizzle.team/docs/get-started/d1-new)
