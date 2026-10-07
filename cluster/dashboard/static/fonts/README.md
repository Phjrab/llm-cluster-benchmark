# Self-hosted Dashboard fonts

Downloaded 2026-10-07 from the authors' official repositories. Binary files are
unmodified WOFF2 assets. All three are SIL Open Font License 1.1; each directory
contains the original `LICENSE.txt`. Checksums and sizes are in `SHA256.json`.

| Family / CSS registration | Release | Official source | File / supported weights |
|---|---|---|---|
| Inter (`Inter`; internal family `Inter Variable`) | 4.1 | https://rsms.me/inter/ · https://github.com/rsms/inter/releases/tag/v4.1 | `web/InterVariable.woff2` from `Inter-4.1.zip`; variable normal 100–900 |
| Pretendard Variable | 1.3.9 | https://github.com/orioncactus/pretendard/releases/tag/v1.3.9 | `web/variable/woff2/PretendardVariable.woff2` from `Pretendard-1.3.9.zip`; variable normal 45–920, as registered by upstream CSS |
| IBM Plex Mono | repository tag 6.4.2 | https://github.com/IBM/plex/tree/v6.4.2/IBM-Plex-Mono | `fonts/complete/woff2/IBMPlexMono-Regular.woff2`; static normal 400 |

Exact download URLs:

- https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip
- https://github.com/orioncactus/pretendard/releases/download/v1.3.9/Pretendard-1.3.9.zip
- https://raw.githubusercontent.com/IBM/plex/v6.4.2/IBM-Plex-Mono/fonts/complete/woff2/IBMPlexMono-Regular.woff2
- IBM license: https://raw.githubusercontent.com/IBM/plex/v6.4.2/LICENSE.txt

`fonts.css` registers `font-display: swap`. No runtime CDN, `local()` preference,
subset modifications, or preload fan-out. Pretendard is the complete official
variable font, so no upstream unicode-range mapping has been changed or omitted.
Only normal UI and regular technical text are needed; italic and extra static
weights are not bundled. Technical text disables synthetic weight/style.

Latin UI and numeric roles use Inter first; Korean falls back to Pretendard.
Technical IDs/code/logs use IBM Plex Mono first and Pretendard for Korean. Korean
log glyphs are proportional: never derive columns or truncation from character
counts. Publication SVG/PNG keeps its separate academic Arial/CJK fallback rules.
