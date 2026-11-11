"""Automagic portfolio server.

Serves the built static site in public/, gates it behind the access form,
stores access emails, Book a Call leads and page visits, and provides a
password-protected admin dashboard at /admin.

Environment:
  ADMIN_PASSWORD  password for /admin (admin is disabled when unset)
  SECRET_KEY      signs the access and admin cookies (set a long random value)
  DATABASE_URL    Postgres URL; when unset, SQLite at $DATA_DIR/portfolio.db
  DATA_DIR        folder for the SQLite file (default ./data)
"""
import csv, hashlib, hmac, io, json, os, re, secrets, sqlite3, time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, quote

from flask import (Flask, Response, abort, g, jsonify, redirect, render_template_string,
                   request, send_file, session)
from itsdangerous import BadSignature, URLSafeSerializer
from werkzeug.middleware.proxy_fix import ProxyFix

ROOT = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(ROOT, "public")
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(ROOT, "data"))
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

app = Flask(__name__, static_folder=None)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config.update(SECRET_KEY=SECRET_KEY, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_NAME="am_admin", PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
                  MAX_CONTENT_LENGTH=32 * 1024)
signer = URLSafeSerializer(SECRET_KEY, salt="am-access")

EMAIL_RE = re.compile(r"^[^\s@]{1,64}@[^\s@]{1,190}\.[^\s@]{2,}$")
BOT_RE = re.compile(r"bot|crawl|spider|slurp|preview|monitor|headless|curl|wget|python-requests", re.I)
OPEN_PAGES = ("/welcome/", "/terms/")
TYPES = ["AI Transformation", "AI Strategy", "AI Product Build", "Not sure yet"]


# ---------------------------------------------------------------- database
def db():
    if "db" not in g:
        if DATABASE_URL:
            import psycopg
            g.db = psycopg.connect(DATABASE_URL, autocommit=True)
        else:
            os.makedirs(DATA_DIR, exist_ok=True)
            g.db = sqlite3.connect(os.path.join(DATA_DIR, "portfolio.db"))
            g.db.execute("PRAGMA journal_mode=WAL")
    return g.db


@app.teardown_appcontext
def _close(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        if not DATABASE_URL:
            conn.commit()
        conn.close()


def q(sql, args=(), one=False):
    """Run SQL written with ? placeholders on either SQLite or Postgres."""
    if DATABASE_URL:
        sql = sql.replace("?", "%s")
    cur = db().execute(sql, args)
    rows = cur.fetchall() if cur.description else []
    return (rows[0] if rows else None) if one else rows


def init_db():
    pk = "SERIAL PRIMARY KEY" if DATABASE_URL else "INTEGER PRIMARY KEY AUTOINCREMENT"
    with app.app_context():
        for sql in (
            f"CREATE TABLE IF NOT EXISTS access (id {pk}, at TEXT NOT NULL, email TEXT NOT NULL, page TEXT, ua TEXT, ip_hash TEXT)",
            f"CREATE TABLE IF NOT EXISTS leads (id {pk}, at TEXT NOT NULL, name TEXT NOT NULL, company TEXT, website TEXT, email TEXT NOT NULL, engagement TEXT, notes TEXT, page TEXT, access_email TEXT)",
            f"CREATE TABLE IF NOT EXISTS visits (id {pk}, at TEXT NOT NULL, path TEXT NOT NULL, ref TEXT, vid TEXT, device TEXT, email TEXT)",
            "CREATE INDEX IF NOT EXISTS visits_at ON visits (at)",
        ):
            q(sql)
        if not DATABASE_URL:
            db().commit()


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ip_hash():
    ip = request.remote_addr or ""
    return hashlib.sha256((SECRET_KEY + ip).encode()).hexdigest()[:16]


def clip(v, n):
    return str(v or "").strip()[:n]


def access_email():
    try:
        return signer.loads(request.cookies.get("am_access", ""))
    except BadSignature:
        return None


# ---------------------------------------------------------------- public API
@app.post("/api/access")
def api_access():
    d = request.get_json(silent=True) or {}
    email = clip(d.get("email"), 254).lower()
    if not EMAIL_RE.match(email):
        return jsonify(ok=False, error="Please enter a valid email address."), 400
    if d.get("agreedTerms") is not True:
        return jsonify(ok=False, error="Please accept the terms to continue."), 400
    page = clip(d.get("page"), 300)
    q("INSERT INTO access (at, email, page, ua, ip_hash) VALUES (?, ?, ?, ?, ?)",
      (now(), email, page, clip(request.user_agent.string, 300), ip_hash()))
    resp = jsonify(ok=True)
    resp.set_cookie("am_access", signer.dumps(email), max_age=180 * 86400, httponly=True,
                    secure=request.is_secure, samesite="Lax")
    return resp


@app.post("/api/lead")
def api_lead():
    d = request.get_json(silent=True) or {}
    if d.get("hp"):  # honeypot field, hidden from people
        return jsonify(ok=True)
    name, email = clip(d.get("name"), 120), clip(d.get("email"), 254).lower()
    if not name:
        return jsonify(ok=False, error="Please enter your full name."), 400
    if not EMAIL_RE.match(email):
        return jsonify(ok=False, error="Please enter a valid email address."), 400
    eng = clip(d.get("engagement"), 40)
    if eng and eng not in TYPES:
        eng = ""
    q("INSERT INTO leads (at, name, company, website, email, engagement, notes, page, access_email) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
      (now(), name, clip(d.get("company"), 160), clip(d.get("website"), 300), email, eng,
       clip(d.get("notes"), 4000), clip(d.get("page"), 300), access_email()))
    return jsonify(ok=True)


@app.post("/api/event")
def api_event():
    ua = request.user_agent.string or ""
    if BOT_RE.search(ua) or session.get("admin"):
        return "", 204
    try:
        d = json.loads(request.get_data(as_text=True) or "{}")
    except ValueError:
        return "", 204
    path = clip(d.get("path"), 300)
    if not path.startswith("/"):
        return "", 204
    ref = urlparse(clip(d.get("ref"), 500)).hostname or ""
    if ref == request.host.split(":")[0]:
        ref = ""
    device = "Phone" if re.search(r"Mobi|iPhone|Android", ua) else "Tablet" if re.search(r"iPad|Tablet", ua) else "Desktop"
    q("INSERT INTO visits (at, path, ref, vid, device, email) VALUES (?, ?, ?, ?, ?, ?)",
      (now(), path, ref, clip(d.get("vid"), 40), device, access_email()))
    return "", 204


@app.get("/healthz")
def healthz():
    return "ok"


# ---------------------------------------------------------------- admin
_fails = defaultdict(list)


def admin_required():
    if not ADMIN_PASSWORD:
        abort(503, "Admin is disabled: set ADMIN_PASSWORD.")
    if not session.get("admin"):
        return redirect("/admin/login")
    return None


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if not ADMIN_PASSWORD:
        abort(503, "Admin is disabled: set ADMIN_PASSWORD.")
    err = ""
    if request.method == "POST":
        key = ip_hash()
        _fails[key] = [t for t in _fails[key] if t > time.time() - 900]
        if len(_fails[key]) >= 8:
            err = "Too many attempts. Try again in 15 minutes."
        elif hmac.compare_digest(request.form.get("password", "").encode(), ADMIN_PASSWORD.encode()):
            session.clear()
            session.permanent = True
            session["admin"] = True
            return redirect("/admin")
        else:
            _fails[key].append(time.time())
            err = "That password is not right."
    return render_template_string(LOGIN_HTML, err=err)


@app.post("/admin/logout")
def admin_logout():
    session.clear()
    return redirect("/admin/login")


@app.get("/admin")
@app.get("/admin/")
def admin_home():
    r = admin_required()
    if r:
        return r
    days = 30
    since = (datetime.now(timezone.utc) - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    since7 = (datetime.now(timezone.utc) - timedelta(days=6)).strftime("%Y-%m-%d")
    visits = q("SELECT at, path, ref, vid, device, email FROM visits WHERE at >= ? ORDER BY at", (since,))
    v7 = [v for v in visits if v[0] >= since7]
    by_day = defaultdict(lambda: [0, set()])
    pages, refs, devices = defaultdict(int), defaultdict(int), defaultdict(int)
    for at, path, ref, vid, dev, _ in visits:
        by_day[at[:10]][0] += 1
        by_day[at[:10]][1].add(vid)
        pages[path] += 1
        refs[ref or "Direct or unknown"] += 1
        devices[dev] += 1
    today = datetime.now(timezone.utc).date()
    series = []
    for i in range(days - 1, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        series.append((d, by_day[d][0], len(by_day[d][1])))
    peak = max([s[1] for s in series] + [1])
    access = q("SELECT at, email, page FROM access ORDER BY at DESC LIMIT 500")
    leads = q("SELECT at, name, company, website, email, engagement, notes, page, access_email FROM leads ORDER BY at DESC LIMIT 500")
    people = q("SELECT email, COUNT(*), MAX(at), COUNT(DISTINCT path) FROM visits WHERE email IS NOT NULL GROUP BY email ORDER BY MAX(at) DESC LIMIT 200")
    tot_access = q("SELECT COUNT(*), COUNT(DISTINCT email) FROM access", one=True)
    tot_leads = q("SELECT COUNT(*) FROM leads", one=True)[0]
    leads7 = q("SELECT COUNT(*) FROM leads WHERE at >= ?", (since7,), one=True)[0]
    access7 = q("SELECT COUNT(DISTINCT email) FROM access WHERE at >= ?", (since7,), one=True)[0]
    top = lambda dct, n=10: sorted(dct.items(), key=lambda x: -x[1])[:n]
    return render_template_string(
        DASH_HTML, series=series, peak=peak, pages=top(pages), refs=top(refs), devices=top(devices, 3),
        total_visits=len(visits), v7=len(v7), u7=len({v[3] for v in v7}), access=access, leads=leads,
        people=people, tot_access=tot_access, tot_leads=tot_leads, leads7=leads7, access7=access7, days=days)


@app.get("/admin/export/<name>.csv")
def admin_export(name):
    r = admin_required()
    if r:
        return r
    cols = {"access": "at, email, page", "leads": "at, name, company, website, email, engagement, notes, page, access_email",
            "visits": "at, path, ref, vid, device, email"}
    if name not in cols:
        abort(404)
    rows = q(f"SELECT {cols[name]} FROM {name} ORDER BY at DESC")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([c.strip() for c in cols[name].split(",")])
    for row in rows:
        w.writerow(["'" + str(c) if str(c or "")[:1] in "=+-@" else c for c in row])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=automagic-{name}.csv"})


# ---------------------------------------------------------------- static site + gate
@app.get("/", defaults={"path": ""})
@app.get("/<path:path>")
def site(path):
    full = os.path.normpath(os.path.join(PUBLIC, path))
    if not full.startswith(PUBLIC):
        abort(404)
    if os.path.isdir(full):
        if path and not path.endswith("/"):
            return redirect("/" + path + "/" + (("?" + request.query_string.decode()) if request.query_string else ""))
        full = os.path.join(full, "index.html")
    if not os.path.isfile(full):
        abort(404)
    if full.endswith(".html"):
        page = "/" + path
        if page not in OPEN_PAGES and not access_email() and not session.get("admin"):
            return redirect("/welcome/?next=" + quote(page))
        resp = send_file(full, max_age=0)
        resp.headers["Cache-Control"] = "no-store"
        return resp
    return send_file(full, max_age=3600)


@app.after_request
def headers(resp):
    resp.headers.setdefault("X-Robots-Tag", "noindex")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    if request.path.startswith("/admin"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------------------------------------------------------------- admin pages
BASE_CSS = """
:root{--bg:#0F151E;--surface:#161F2B;--ink:#E5EBF2;--muted:#9AA8B9;--line:#263243;--navy:#A9BEDA;--cyan:#7FCFE0;--cyan-soft:#16303A;--band:#18263A;color-scheme:dark}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 Inter,system-ui,-apple-system,"Segoe UI",sans-serif}
a{color:var(--cyan)}h1,h2{font-family:"Space Grotesk",Inter,sans-serif;font-weight:600;letter-spacing:-.01em}
.mono{font-family:"IBM Plex Mono",ui-monospace,Menlo,monospace;font-size:11.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
button,.btn{font:600 14px Inter,sans-serif;border:0;border-radius:999px;padding:10px 18px;background:var(--cyan);color:#0F151E;cursor:pointer;text-decoration:none;display:inline-block}
.ghost{background:transparent;color:var(--muted);border:1px solid var(--line)}
"""
FONTS = '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@600&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">'

LOGIN_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Portfolio Admin</title><meta name="robots" content="noindex">""" + FONTS + """<style>""" + BASE_CSS + """
main{min-height:100vh;display:grid;place-items:center;padding:16px}
form{width:min(100%,380px);background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:28px}
label{display:block;margin:18px 0 8px}input{width:100%;font:16px Inter,sans-serif;color:var(--ink);background:var(--bg);border:1.5px solid var(--line);border-radius:10px;padding:12px}
input:focus{outline:none;border-color:var(--cyan)}button{width:100%;margin-top:16px;padding:13px}.err{color:#F2B8B5;font-size:14px;min-height:20px;margin:10px 0 0}
</style></head><body><main><form method="post"><p class="mono">Automagic portfolio</p><h1 style="margin:4px 0 0">Admin sign in</h1>
<label for="pw" class="mono">Password</label><input id="pw" name="password" type="password" autocomplete="current-password" required autofocus>
<p class="err" role="alert">{{ err }}</p><button type="submit">Sign in</button></form></main></body></html>"""

DASH_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Portfolio Admin</title><meta name="robots" content="noindex">""" + FONTS + """<style>""" + BASE_CSS + """
header{background:var(--band);border-bottom:1px solid var(--line)}
.wrap{max-width:1180px;margin:0 auto;padding:0 16px}
header .wrap{display:flex;align-items:center;justify-content:space-between;gap:12px;height:60px}
header h1{font-size:18px;margin:0}header form{margin:0}
nav.tabs{display:flex;gap:6px;flex-wrap:wrap;margin:24px 0 8px}
nav.tabs a{padding:7px 14px;border-radius:999px;border:1px solid var(--line);color:var(--muted);text-decoration:none;font-size:14px}
nav.tabs a:hover{color:var(--ink)}
section{margin:28px 0}h2{font-size:22px;margin:0 0 12px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
h2 a{font:500 13px Inter,sans-serif}
.tiles{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
@media (max-width:820px){.tiles{grid-template-columns:repeat(2,minmax(0,1fr))}}
.tile{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:16px 18px}
.tile b{display:block;font:600 30px "Space Grotesk",sans-serif;margin:6px 0 2px}.tile span{color:var(--muted);font-size:13px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:18px}
.chart{display:flex;align-items:flex-end;gap:3px;height:160px;padding-top:8px}
.chart div{flex:1;background:var(--cyan);border-radius:3px 3px 0 0;min-height:2px;opacity:.85}
.chart div:hover{opacity:1}
.axis{display:flex;justify-content:space-between;color:var(--muted);font-size:12px;margin-top:6px}
.grid3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
@media (max-width:900px){.grid3{grid-template-columns:minmax(0,1fr)}}
.bars{list-style:none;margin:0;padding:0;display:grid;gap:8px}
.bars li{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:4px 10px;font-size:14px}
.bars li span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.bars li i{grid-column:1/-1;height:5px;border-radius:3px;background:var(--line);overflow:hidden}
.bars li i b{display:block;height:100%;background:var(--navy)}
.tw{overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:var(--surface)}
table{border-collapse:collapse;width:100%;font-size:14px}th{font:500 11px "IBM Plex Mono",monospace;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);text-align:left;padding:10px 12px;background:var(--cyan-soft);white-space:nowrap}
td{padding:10px 12px;border-top:1px solid var(--line);vertical-align:top}td.nw{white-space:nowrap}
.note{white-space:pre-wrap;max-width:420px;color:var(--muted)}
.empty{color:var(--muted);padding:16px}
.pill{font:500 11px "IBM Plex Mono",monospace;padding:2px 8px;border-radius:999px;background:var(--cyan-soft);color:var(--cyan);white-space:nowrap}
</style></head><body>
<header><div class="wrap"><h1>Automagic portfolio · Admin</h1><form method="post" action="/admin/logout"><button class="ghost" type="submit">Sign out</button></form></div></header>
<main class="wrap">
<nav class="tabs" aria-label="Sections"><a href="#overview">Overview</a><a href="#leads">Leads ({{ tot_leads }})</a><a href="#access">Access emails ({{ tot_access[1] }})</a><a href="#people">Visitor activity</a><a href="/" target="_blank">Open site</a></nav>

<section id="overview"><h2>Last 7 days</h2>
<div class="tiles">
<div class="tile"><span class="mono">Page views</span><b>{{ v7 }}</b><span>{{ total_visits }} in {{ days }} days</span></div>
<div class="tile"><span class="mono">Unique visitors</span><b>{{ u7 }}</b><span>by browser</span></div>
<div class="tile"><span class="mono">New access sign-ups</span><b>{{ access7 }}</b><span>{{ tot_access[1] }} unique emails in total</span></div>
<div class="tile"><span class="mono">Book a Call leads</span><b>{{ leads7 }}</b><span>{{ tot_leads }} in total</span></div>
</div></section>

<section><h2>Page views, last {{ days }} days <a href="/admin/export/visits.csv">Download CSV</a></h2>
<div class="card"><div class="chart" role="img" aria-label="Daily page views for the last {{ days }} days">
{% for d, n, u in series %}<div style="height:{{ (n / peak * 100)|round(1) }}%" title="{{ d }}: {{ n }} views, {{ u }} visitors"></div>{% endfor %}
</div><div class="axis"><span>{{ series[0][0] }}</span><span>Today</span></div></div></section>

<section><div class="grid3">
<div class="card"><p class="mono">Top pages</p>{% if pages %}<ul class="bars">{% for k, n in pages %}<li><span>{{ k }}</span><span>{{ n }}</span><i><b style="width:{{ (n / pages[0][1] * 100)|round }}%"></b></i></li>{% endfor %}</ul>{% else %}<p class="empty">No visits yet.</p>{% endif %}</div>
<div class="card"><p class="mono">Referrers</p>{% if refs %}<ul class="bars">{% for k, n in refs %}<li><span>{{ k }}</span><span>{{ n }}</span><i><b style="width:{{ (n / refs[0][1] * 100)|round }}%"></b></i></li>{% endfor %}</ul>{% else %}<p class="empty">No visits yet.</p>{% endif %}</div>
<div class="card"><p class="mono">Devices</p>{% if devices %}<ul class="bars">{% for k, n in devices %}<li><span>{{ k }}</span><span>{{ n }}</span><i><b style="width:{{ (n / devices[0][1] * 100)|round }}%"></b></i></li>{% endfor %}</ul>{% else %}<p class="empty">No visits yet.</p>{% endif %}</div>
</div></section>

<section id="leads"><h2>Book a Call leads <a href="/admin/export/leads.csv">Download CSV</a></h2>
{% if leads %}<div class="tw"><table><thead><tr><th>Received (UTC)</th><th>Name</th><th>Company</th><th>Email</th><th>Engagement</th><th>Anything we should know</th></tr></thead><tbody>
{% for at, name, company, website, email, eng, notes, page, ae in leads %}<tr><td class="nw">{{ at[:16]|replace('T',' ') }}</td><td>{{ name }}</td><td>{{ company }}{% if website %}<br><a href="{{ website if website.startswith('http') else 'https://' ~ website }}" target="_blank" rel="noopener noreferrer">{{ website }}</a>{% endif %}</td><td><a href="mailto:{{ email }}">{{ email }}</a>{% if ae and ae != email %}<br><span class="mono">access: {{ ae }}</span>{% endif %}</td><td>{% if eng %}<span class="pill">{{ eng }}</span>{% endif %}</td><td class="note">{{ notes }}</td></tr>{% endfor %}
</tbody></table></div>{% else %}<div class="card empty">No leads yet. They appear here when someone sends the Book a Call form.</div>{% endif %}</section>

<section id="access"><h2>Access emails <a href="/admin/export/access.csv">Download CSV</a></h2>
{% if access %}<div class="tw"><table><thead><tr><th>Signed up (UTC)</th><th>Email</th><th>First page asked for</th></tr></thead><tbody>
{% for at, email, page in access %}<tr><td class="nw">{{ at[:16]|replace('T',' ') }}</td><td><a href="mailto:{{ email }}">{{ email }}</a></td><td>{{ page }}</td></tr>{% endfor %}
</tbody></table></div>{% else %}<div class="card empty">No sign-ups yet.</div>{% endif %}</section>

<section id="people"><h2>Visitor activity by email</h2>
{% if people %}<div class="tw"><table><thead><tr><th>Email</th><th>Page views</th><th>Pages seen</th><th>Last seen (UTC)</th></tr></thead><tbody>
{% for email, n, last, distinct in people %}<tr><td>{{ email }}</td><td>{{ n }}</td><td>{{ distinct }}</td><td class="nw">{{ last[:16]|replace('T',' ') }}</td></tr>{% endfor %}
</tbody></table></div>{% else %}<div class="card empty">No signed-in visits yet.</div>{% endif %}</section>
</main></body></html>"""

init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=False)
