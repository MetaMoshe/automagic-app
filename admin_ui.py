"""Shared look for the admin: base CSS, fonts, and the page shell with the left menu.

Every admin page is `head(active, title) + body + FOOT`. The menu badges come from
`admin_nav`, which admin.py injects into every template.
"""

BASE_CSS = """
:root{--bg:#0F151E;--surface:#161F2B;--ink:#E5EBF2;--muted:#9AA8B9;--line:#263243;--navy:#A9BEDA;--cyan:#7FCFE0;--cyan-soft:#16303A;--band:#18263A;color-scheme:dark}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 Inter,system-ui,-apple-system,"Segoe UI",sans-serif}
a{color:var(--cyan)}h1,h2{font-family:"Space Grotesk",Inter,sans-serif;font-weight:600;letter-spacing:-.01em}
.mono{font-family:"IBM Plex Mono",ui-monospace,Menlo,monospace;font-size:11.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
button,.btn{font:600 14px Inter,sans-serif;border:0;border-radius:999px;padding:10px 18px;background:var(--cyan);color:#0F151E;cursor:pointer;text-decoration:none;display:inline-block}
.ghost{background:transparent;color:var(--muted);border:1px solid var(--line)}
"""
FONTS = '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@600&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">'

ADMIN_CSS = """
/* shell */
.adm{display:grid;grid-template-columns:240px minmax(0,1fr);min-height:100vh}
.side{position:sticky;top:0;height:100vh;display:flex;flex-direction:column;gap:4px;background:var(--band);border-right:1px solid var(--line);padding:18px 12px;overflow-y:auto}
.side .brand{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:4px 10px 16px;font:600 17px "Space Grotesk",sans-serif;color:var(--ink);text-decoration:none}
.side .brand small{display:block;font:500 10.5px "IBM Plex Mono",monospace;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
.side .grp{font:500 10.5px "IBM Plex Mono",monospace;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);padding:14px 10px 6px;opacity:.8}
.side a.it{display:flex;align-items:center;gap:10px;padding:8px 10px;border-radius:9px;color:var(--muted);text-decoration:none;font-size:14.5px}
.side a.it svg{flex:none;opacity:.85}
.side a.it:hover{color:var(--ink);background:rgba(255,255,255,.04)}
.side a.it.on{color:var(--ink);background:var(--cyan-soft);box-shadow:inset 3px 0 0 var(--cyan)}
.side a.it .n{margin-left:auto;font:600 11px "IBM Plex Mono",monospace;min-width:20px;text-align:center;padding:1px 7px;border-radius:999px;background:var(--cyan);color:#0F151E}
.side .foot{margin-top:auto;padding-top:14px;border-top:1px solid var(--line);display:grid;gap:2px}
.side .foot form{margin:0}.side .foot button{width:100%;text-align:left;background:transparent;color:var(--muted);font:500 14.5px Inter,sans-serif;padding:8px 10px;border-radius:9px;display:flex;gap:10px;align-items:center}
.side .foot button:hover{color:var(--ink);background:rgba(255,255,255,.04)}
.menu-btn{display:none}
.main{min-width:0}
.wrap{max-width:1180px;margin:0 auto;padding:8px 28px 48px}
@media (max-width:900px){
  .adm{grid-template-columns:minmax(0,1fr)}
  .side{position:sticky;z-index:20;height:auto;flex-direction:column;padding:10px 12px;border-right:0;border-bottom:1px solid var(--line)}
  .side .brand{padding:4px 4px}
  .menu-btn{display:inline-flex;align-items:center;gap:8px;padding:7px 12px;font-size:13px}
  .side .nav{display:none}.side.open .nav{display:flex;flex-direction:column;gap:2px;padding-bottom:8px}
  .side .foot{margin-top:6px}
  .wrap{padding:4px 16px 40px}
}
/* page */
section{margin:24px 0}h2{font-size:24px;margin:0 0 12px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
h2 a{font:500 13px Inter,sans-serif}
.ph{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin:22px 0 6px}
.ph h2{margin:0}.ph p{margin:4px 0 0;color:var(--muted);font-size:14px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:20px}
.tw{overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:var(--surface)}
table{border-collapse:collapse;width:100%;font-size:14px}th{font:500 11px "IBM Plex Mono",monospace;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);text-align:left;padding:10px 12px;background:var(--cyan-soft);white-space:nowrap}
td{padding:10px 12px;border-top:1px solid var(--line);vertical-align:middle}td.nw{white-space:nowrap}
tr.rowlink{cursor:pointer}tr.rowlink:hover td{background:rgba(127,207,224,.05)}
.note{white-space:pre-wrap;max-width:420px;color:var(--muted)}
.pill{font:500 11px "IBM Plex Mono",monospace;padding:2px 8px;border-radius:999px;background:var(--cyan-soft);color:var(--cyan);white-space:nowrap}
.pill.off{background:transparent;border:1px solid var(--line);color:var(--muted)}
.pill.won{background:rgba(127,211,166,.14);color:#7FD3A6}.pill.lost{background:transparent;border:1px solid #5a2a2a;color:#F2B8B5}
.empty{color:var(--muted)}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin:12px 0 16px}
.chips a{padding:6px 12px;border-radius:999px;border:1px solid var(--line);color:var(--muted);text-decoration:none;font-size:13.5px}
.chips a:hover{color:var(--ink)}.chips a.on{color:var(--ink);border-color:var(--cyan);background:var(--cyan-soft)}
.chips a b{font-weight:600;margin-left:4px;color:var(--ink)}
/* forms */
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px 18px}
.grid .full{grid-column:1/-1}
@media (max-width:760px){.grid{grid-template-columns:minmax(0,1fr)}}
label.f{display:block;font:500 11px "IBM Plex Mono",monospace;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin:0 0 6px}
input[type=text],input[type=email],input[type=url],input[type=tel],input[type=password],input[type=search],select,textarea{width:100%;font:15px Inter,sans-serif;color:var(--ink);background:var(--bg);border:1.5px solid var(--line);border-radius:10px;padding:10px 12px}
textarea{min-height:110px;resize:vertical}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--cyan)}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.check{display:flex;gap:10px;align-items:center;font-size:14.5px}
.err{color:#F2B8B5}.ok{color:#7FD3A6}
.danger{background:transparent;color:#F2B8B5;border:1px solid #5a2a2a}
.secret{font:500 16px "IBM Plex Mono",monospace;background:var(--bg);border:1px dashed var(--cyan);border-radius:10px;padding:12px 14px;user-select:all}
.drop{border:1.5px dashed var(--line);border-radius:12px;padding:22px;text-align:center;color:var(--muted);transition:border-color .15s,background .15s;cursor:pointer}
.drop.over{border-color:var(--cyan);background:rgba(127,207,224,.06);color:var(--ink)}
.drop b{color:var(--ink)}
.files{list-style:none;margin:12px 0 0;padding:0;display:grid;gap:8px}
.files li{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:12px;align-items:center;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:var(--bg)}
.files .ic{font:600 10px "IBM Plex Mono",monospace;width:38px;height:44px;border-radius:5px;display:grid;place-items:end center;padding-bottom:6px;background:var(--cyan-soft);color:var(--cyan)}
.files .nm{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.files .nm small{display:block;color:var(--muted);font-size:12.5px}
.files button{padding:6px 12px;font-size:13px}
.split{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(0,1fr);gap:18px;align-items:start}
@media (max-width:960px){.split{grid-template-columns:minmax(0,1fr)}}
dl.kv{display:grid;grid-template-columns:140px minmax(0,1fr);gap:8px 14px;margin:0;font-size:14.5px}
dl.kv dt{font:500 11px "IBM Plex Mono",monospace;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);padding-top:3px}
dl.kv dd{margin:0;overflow-wrap:anywhere}
@media (max-width:560px){dl.kv{grid-template-columns:minmax(0,1fr)}dl.kv dd{margin-bottom:6px}}
/* analytics */
.tiles{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
@media (max-width:820px){.tiles{grid-template-columns:repeat(2,minmax(0,1fr))}}
.tile{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:16px 18px;text-decoration:none;color:inherit;display:block}
a.tile:hover{border-color:var(--cyan)}
.tile b{display:block;font:600 30px "Space Grotesk",sans-serif;margin:6px 0 2px}.tile span{color:var(--muted);font-size:13px}
.chart{display:flex;align-items:flex-end;gap:3px;height:160px;padding-top:8px}
.chart div{flex:1;background:var(--cyan);border-radius:3px 3px 0 0;min-height:2px;opacity:.85}
.chart div:hover{opacity:1}
.axis{display:flex;justify-content:space-between;color:var(--muted);font-size:12px;margin-top:6px}
.grid2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.grid3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
@media (max-width:900px){.grid2,.grid3{grid-template-columns:minmax(0,1fr)}}
.bars{list-style:none;margin:0;padding:0;display:grid;gap:8px}
.bars li{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:4px 10px;font-size:14px}
.bars li span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.bars li i{grid-column:1/-1;height:5px;border-radius:3px;background:var(--line);overflow:hidden}
.bars li i b{display:block;height:100%;background:var(--navy)}
/* inbox */
.feed{list-style:none;margin:0;padding:0;border:1px solid var(--line);border-radius:12px;background:var(--surface);overflow:hidden}
.feed li{border-top:1px solid var(--line)}.feed li:first-child{border-top:0}
.feed a.fi{display:grid;grid-template-columns:36px minmax(0,1fr) auto;gap:12px;align-items:center;padding:12px 16px;color:inherit;text-decoration:none}
.feed a.fi:hover{background:rgba(127,207,224,.05)}
.feed .ico{width:36px;height:36px;border-radius:10px;display:grid;place-items:center;background:var(--bg);border:1px solid var(--line);color:var(--muted)}
.feed .un .ico{background:var(--cyan-soft);border-color:transparent;color:var(--cyan)}
.feed .tx{min-width:0}.feed .tx b{display:block;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.feed .un .tx b{font-weight:600}
.feed .tx span{display:block;color:var(--muted);font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.feed time{font:500 11.5px "IBM Plex Mono",monospace;color:var(--muted);white-space:nowrap;display:flex;align-items:center;gap:8px}
.feed .un time::before{content:"";width:8px;height:8px;border-radius:50%;background:var(--cyan)}
"""

_I = {
    "pricing": '<path d="M3 3h8l10 10-8 8L3 11z"/><circle cx="7.5" cy="7.5" r="1.5"/>',
    "inbox": '<path d="M3 13h5l1.5 3h5L16 13h5"/><path d="M5 5h14l2 8v6H3v-6z"/>',
    "chart": '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    "leads": '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c0-3.6 2.9-6 6.5-6s6.5 2.4 6.5 6"/><path d="M17 8h5M19.5 5.5v5"/>',
    "clients": '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3 13h18"/>',
    "consultants": '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7"/>',
    "visitors": '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "site": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.7 3.8 5.7 3.8 9s-1.3 6.3-3.8 9c-2.5-2.7-3.8-5.7-3.8-9S9.5 5.7 12 3z"/>',
    "portal": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M8 14h4"/>',
    "out": '<path d="M15 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4M10 17l5-5-5-5M15 12H4"/>',
    "menu": '<path d="M4 7h16M4 12h16M4 17h16"/>',
    "lead": '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7"/>',
    "upload": '<path d="M12 16V4M7 9l5-5 5 5M4 20h16"/>',
    "msg": '<path d="M4 5h16v11H9l-5 4z"/>',
    "sent": '<path d="m3 11 18-8-8 18-2-8z"/>',
    "key": '<circle cx="8" cy="15" r="4"/><path d="m11 12 9-9M17 6l3 3"/>',
}


def ico(name, size=18):
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{_I[name]}</svg>')


# (group, key, label, href, badge key)
MENU = [
    ("Workspace", "chart", "Analytics", "/admin", None),
    ("Workspace", "inbox", "Inbox", "/admin/inbox", "inbox"),
    ("Sales", "leads", "Lead CRM", "/admin/leads", "leads"),
    ("Delivery", "clients", "Clients", "/admin/clients", "clients"),
    ("Delivery", "consultants", "Consultants", "/admin/consultants", None),
    ("Delivery", "pricing", "Pricing page", "/admin/pricing", None),
    ("Audience", "visitors", "Access and visitors", "/admin/visitors", None),
]


def _menu(active):
    out, grp = [], None
    for g, key, label, href, badge in MENU:
        if g != grp:
            out.append(f'<p class="grp">{g}</p>')
            grp = g
        on = ' on" aria-current="page' if key == active else ""
        n = (f'{{% if admin_nav.{badge} %}}<span class="n" aria-label="{{{{ admin_nav.{badge} }}}} new">'
             f'{{{{ admin_nav.{badge} }}}}</span>{{% endif %}}') if badge else ""
        out.append(f'<a class="it{on}" href="{href}">{ico(key)}<span>{label}</span>{n}</a>')
    return "".join(out)


def head(active, title):
    return ("""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>""" + title + """ · Automagic Admin</title><meta name="robots" content="noindex">""" + FONTS + "<style>" + BASE_CSS + ADMIN_CSS + """</style></head><body>
<div class="adm"><aside class="side" id="side">
 <div class="row" style="justify-content:space-between;flex-wrap:nowrap"><a class="brand" href="/admin"><span><img src="/brand/wordmark-light.svg" alt="Automagic" width="120" height="25" style="display:block;height:22px;width:auto"><small style="margin-top:4px">Admin</small></span></a>
 <button class="ghost menu-btn" type="button" aria-expanded="false" aria-controls="side-nav" onclick="var s=document.getElementById('side');s.classList.toggle('open');this.setAttribute('aria-expanded',s.classList.contains('open'))">""" + ico("menu", 16) + """Menu</button></div>
 <nav class="nav" id="side-nav" aria-label="Admin">""" + _menu(active) + """
  <div class="foot">
   <a class="it" href="/" target="_blank" rel="noopener">""" + ico("site") + """<span>Open website</span></a>
   <a class="it" href="/portal/" target="_blank" rel="noopener">""" + ico("portal") + """<span>Client portal</span></a>
   <form method="post" action="/admin/logout"><button type="submit">""" + ico("out") + """Sign out</button></form>
  </div>
 </nav>
</aside>
<main class="main"><div class="wrap">
""")


FOOT = "</div></main></div></body></html>"
