# Automagic portfolio site

A static site that joins the Automagic client portfolio and the case studies
(Hunter Executive, Zugi.ai, Revscale) with a shared top navigation and
in-site cross-links.

- `src/` holds each page's HTML exactly as published from its claude.ai artifact.
- `build.py` adds the navigation bar and "keep exploring" cards, rewrites
  artifact links to in-site paths, and writes `public/`.
- `render.yaml` deploys `public/` as a Render static site.

To update a page: replace its file in `src/` with the artifact's latest HTML,
run `python3 build.py`, commit and push. Render redeploys on push.

To add a case study: add its file to `src/`, add a row to `PAGES` in
`build.py` (slug, file, nav label, artifact id, one-line blurb), and rebuild.
