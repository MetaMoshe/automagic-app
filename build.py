#!/usr/bin/env python3
"""Build the static portfolio site.

Takes the artifact HTML in src/ (copied verbatim from the published
claude.ai artifacts), adds a shared top navigation bar and a "more case
studies" strip, rewrites artifact links to in-site paths, and writes the
result to public/. Run:  python3 build.py
"""
import pathlib, re, shutil

ROOT = pathlib.Path(__file__).parent
SRC, OUT = ROOT / "src", ROOT / "public"

# slug, source file, nav label, artifact id (for link rewriting), blurb
PAGES = [
    ("", "portfolio.html", "Portfolio", "Xwe1YVfH51s8fDtv4XzFEv",
     "Every engagement at a glance"),
    ("hunter", "hunter.html", "Hunter Executive", "TLYDnbsNc4ojm2kMQSThz9",
     "Executive search · discovery findings and the PESA platform"),
    ("zugi", "zugi.html", "Zugi.ai", "H8UgAvWC9srxh36TgemtzZ",
     "Private markets · AI investment reasoning, end to end"),
    ("revscale", "revscale.html", "Revscale", "Aj6gQzm2QS64LarUQjnyr1",
     "Franchising · AI agent workforce and FranchiseHub"),
]

def href(slug):
    return "/" if not slug else f"/{slug}/"

LOGO = ('<svg viewBox="0 0 40 40" aria-hidden="true"><defs><linearGradient id="amlg" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#C8CED4"/><stop offset="1" stop-color="#B6E6F0"/></linearGradient></defs>'
        '<path d="M31 9.5A15 15 0 1 0 31 30.5" fill="none" stroke="url(#amlg)" stroke-width="6" stroke-linecap="square"/>'
        '<rect x="31" y="5" width="5" height="5" fill="#B6E6F0"/><rect x="31" y="30" width="5" height="5" fill="#C8CED4"/></svg>')

CSS = """<style id="am-nav-css">
:root{--am-nav-h:56px}
html{scroll-padding-top:calc(var(--am-nav-h) + 24px)}
.am-nav{position:sticky;top:0;z-index:40;background:var(--navy-deep,#2C3C53);color:#F4F7FA;border-bottom:1px solid rgba(255,255,255,.12);padding-top:env(safe-area-inset-top,0px)}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]) .am-nav{background:#0B1018}}
:root[data-theme="dark"] .am-nav{background:#0B1018}
.am-nav-in{max-width:1180px;margin:0 auto;height:var(--am-nav-h);display:flex;align-items:center;gap:20px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
.am-home{display:flex;align-items:center;gap:9px;color:inherit;text-decoration:none;font-family:var(--font-display);font-weight:600;font-size:16px;white-space:nowrap;flex:none}
.am-home svg{width:24px;height:24px}
.am-links{display:flex;gap:4px;overflow-x:auto;scrollbar-width:none;margin-left:auto;min-width:0}
.am-links::-webkit-scrollbar{display:none}
.am-links a{font-family:var(--font-body);font-size:14px;font-weight:500;color:#C5D0DC;text-decoration:none;padding:7px 12px;border-radius:999px;white-space:nowrap}
.am-links a:hover{color:#fff;background:rgba(255,255,255,.08)}
.am-links a[aria-current="page"]{color:#1F2B3D;background:#B6E6F0}
@media (max-width:640px){.am-home span{display:none}.am-nav-in{gap:10px}.am-links a{font-size:13px;padding:6px 10px}}
.band .topbar .brand{display:none}
.band .topbar{justify-content:flex-start;padding-block:18px 0}
@media (max-width:640px){.am-links a{padding:6px 8px}}
.rail{top:calc(var(--am-nav-h) + 24px)!important;max-height:calc(100vh - var(--am-nav-h) - 48px)!important}
.proj{scroll-margin-top:calc(var(--am-nav-h) + 24px)!important}
.am-more{border-top:1px solid var(--line);padding-block:40px 8px}
.am-more h2{font-family:var(--font-display);font-size:22px;margin:0 0 16px;color:var(--navy-deep)}
.am-cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
@media (max-width:820px){.am-cards{grid-template-columns:minmax(0,1fr)}}
.am-card{display:block;text-decoration:none;border:1px solid var(--line);border-radius:12px;background:var(--surface);padding:16px 18px;min-width:0}
.am-card:hover{border-color:var(--navy)}
.am-card b{display:block;font-family:var(--font-display);font-size:17px;color:var(--navy-deep);margin-bottom:4px}
.am-card span{font-size:13.5px;color:var(--muted);line-height:1.45}
.am-card i{font-style:normal;display:block;margin-top:10px;font-family:var(--font-mono);font-size:11.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--cyan-ink)}
</style>"""


def nav(active):
    links = "".join(
        '<a href="%s"%s>%s</a>' % (href(s), ' aria-current="page"' if s == active else '', label)
        for s, _, label, _, _ in PAGES)
    return (f'<nav class="am-nav" aria-label="Site"><div class="am-nav-in">'
            f'<a class="am-home" href="/">{LOGO}<span>Automagic AI</span></a>'
            f'<div class="am-links">{links}</div></div></nav>')


def more(active):
    cards = "".join(
        f'<a class="am-card" href="{href(s)}"><b>{label}</b><span>{blurb}</span>'
        f'<i>{"Back to portfolio" if not s else "Read the case study"} →</i></a>'
        for s, _, label, _, blurb in PAGES if s != active and (s or active))
    title = "More case studies" if not active else "Keep exploring"
    return f'<div class="wrap am-more"><h2>{title}</h2><div class="am-cards">{cards}</div></div>'


DROP_HUNTER = ["business-case", "investment-summary", "next-steps", "p-summary", "p-engagement-model",
               "p-fixed-costs", "p-running-and-third-party-costs-paid-by-hunter", "p-terms"]


def must(html, old, new):
    assert old in html, "Hunter clean-up: text not found: " + old[:60]
    return html.replace(old, new)


def clean_hunter(html):
    """Public version of the Hunter report: drop commercial terms and confidential labels."""
    for sid in DROP_HUNTER:
        html, n = re.subn(r'<section class="sec[^"]*" id="%s".*?</section>\s*' % sid, "", html, flags=re.S)
        assert n == 1, sid
        html = re.sub(r'<li><a href="#%s">.*?</a></li>' % sid, "", html)
    html, n = re.subn(r'<div class="part" id="proposal">.*?</div>\s*', "", html, flags=re.S)
    assert n == 1
    html, n = re.subn(r'<h5>Stage 1 proposal</h5>\s*<ol>\s*</ol>\s*', "", html)
    assert n == 1
    html = must(html, '<p class="kicker">Part three</p>', '<p class="kicker">Part two</p>')
    html, n = re.subn(r'<p class="note">.*?</p>',
        '<p class="note">This case study presents the Discovery Findings report Automagic delivered to Hunter Executive, '
        'with commercial terms removed. The PESA platform it proposed was then built and rolled out in three waves.</p>',
        html, count=1, flags=re.S)
    assert n == 1
    html = must(html, "Client report · Confidential", "Client case study · AI transformation")
    html = must(html, "Prepared 10 November 2026 · Confidential", "Published November 2026")
    html = must(html, "how Stage 1 is scoped, sequenced and priced", "how Stage 1 was scoped and sequenced")
    html, n = re.subn(r'<p><strong>The case and the cost\.</strong>.*?</p>',
        '<p><strong>The case.</strong> Stage 1 returns about 7 hours a week to each of 12 users and about three extra '
        'mandates a year, and on expected assumptions pays back in about 12 months. The engagement is a hybrid of a '
        'Fractional Transformation Lead, a monthly consultancy fee and a fixed cost per agent; Hunter owns what is built '
        'and pays providers directly.</p>', html, count=1, flags=re.S)
    assert n == 1
    # Roadmap: drop the running-cost column of the "After Stage 1" table.
    html = must(html, "<th>Indicative effort</th><th>Indicative running cost</th>", "<th>Indicative effort</th>")
    html, n = re.subn(r'(capabilities(?:, built alongside agent use)?</td>)<td>[^<]*(?:<span class="tbc">\$XXX</span>[^<]*)+</td>',
                      r"\1", html)
    assert n == 2, n
    html = must(html, '<span class="tbc">$XXX</span> a year (3 mandates × average fee)', "3 mandates a year × average fee")
    html = re.sub(r'loaded hourly cost \(<span class="tbc">\$XXX</span>\)', "loaded hourly cost", html)
    html = html.replace(", as set out in the investment summary", "")
    # Renumber the section eyebrows after the removals.
    k = iter(range(1, 100))
    html = re.sub(r'<p class="eyebrow">Section \d+</p>', lambda m: '<p class="eyebrow">Section %02d</p>' % next(k), html)
    assert "$XXX" not in html and "Confidential" not in html, "leftover"
    return html


def build():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    ids = {aid: href(s) for s, _, _, aid, _ in PAGES}
    for slug, src, label, _, _ in PAGES:
        html = (SRC / src).read_text(encoding="utf-8")
        # The artifact puts <title> inside <body>; move it into <head>.
        m = re.search(r"<title>.*?</title>", html, re.S)
        title = m.group(0) if m else f"<title>{label} · Automagic AI</title>"
        if m:
            html = html.replace(title, "", 1)
        html = html.replace("</head>", title + CSS + "</head>", 1)
        if slug == "hunter":
            html = clean_hunter(html)
        html = html.replace("<body>", "<body>" + nav(slug), 1)
        # Cross-links: claude.ai artifact URLs -> in-site pages, opened in place.
        for aid, path in ids.items():
            html = html.replace(f"https://claude.ai/artifact/{aid}", path)
        html = html.replace(
            """href="'+esc(c.link.href)+'" target="_blank" rel="noopener">""",
            """href="'+esc(c.link.href)+'"'+(/^\\//.test(c.link.href)?'':' target="_blank" rel="noopener"')+'>""")
        # "More case studies" strip just above the page footer.
        html = html.replace('<footer><div class="wrap">', more(slug) + '<footer><div class="wrap">', 1)
        dest = OUT / slug / "index.html" if slug else OUT / "index.html"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(html, encoding="utf-8")
        print(f"built {dest.relative_to(ROOT)} ({len(html)//1024} KB)")


if __name__ == "__main__":
    build()
