# Pinned local browser dependencies

All assets are served locally; the reader makes no CDN requests.

| Library | Version | Retained assets | License |
| --- | --- | --- | --- |
| KaTeX | 0.16.22 | Minified renderer, auto-render, CSS, fonts | `katex/LICENSE` (MIT) |
| marked | 15.0.12 | UMD Markdown parser | `marked/LICENSE.md` (MIT) |
| DOMPurify | 3.2.6 | Minified HTML sanitizer | `dompurify/LICENSE` (Apache 2.0 or MPL 2.0) |

Assets were copied from the exact version's official npm registry tarball.
KaTeX runs with `trust: false`; Markdown HTML is sanitized before insertion.
Images, embedded documents, forms, inline styles and executable content are
not accepted from exposition Markdown. Links open separately with noopener.
