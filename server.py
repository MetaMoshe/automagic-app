"""Automagic portfolio server.

Serves the built static site in public/, gates it behind the access form,
stores access emails, Book a Call leads and page visits, and provides a
password-protected admin dashboard at /admin.

Environment:
  ADMIN_PASSWORD  password for /admin (admin is disabled when unset)
  STRIPE_SECRET_KEY  optional; shows Stripe invoices in the client portal and admin
  SECRET_KEY      signs the access and admin cookies (set a long random value)
  DATABASE_URL    Postgres URL; when unset, SQLite at $DATA_DIR/portfolio.db
  DATA_DIR        folder for the SQLite file (default ./data)
"""
import csv, gzip, hashlib, hmac, io, json, os, re, secrets, shutil, sqlite3, threading, time, urllib.request
from functools import lru_cache
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
                  MAX_CONTENT_LENGTH=27 * 1024 * 1024)  # uploads; other routes are capped at 64 KB in portal.guards
signer = URLSafeSerializer(SECRET_KEY, salt="am-access")

EMAIL_RE = re.compile(r"^[^\s@]{1,64}@[^\s@]{1,190}\.[^\s@]{2,}$")
BOT_RE = re.compile(r"bot|crawl|spider|slurp|preview|monitor|headless|curl|wget|python-requests", re.I)
OPEN_PAGES = ("/", "/welcome/", "/terms/", "/login/")
INDEXED_PAGES = ("/",)  # everything else stays out of search engines
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
        for table in ("access", "visits"):
            for col in ("country", "city"):
                try:
                    q(f"ALTER TABLE {table} ADD COLUMN {'IF NOT EXISTS ' if DATABASE_URL else ''}{col} TEXT")
                except sqlite3.OperationalError:
                    pass  # column already there
        if not DATABASE_URL:
            db().commit()


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- geolocation
# Country and city come from the free DB-IP City Lite database (CC BY 4.0), downloaded
# once a month to DATA_DIR. Only the place name is stored, never the IP address.
GEO_FILE = os.path.join(DATA_DIR, "dbip-city-lite.mmdb")
_geo = {"reader": None, "mtime": 0}
_geo_lock = threading.Lock()


def _geo_download():
    lock = GEO_FILE + ".lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if time.time() - os.path.getmtime(lock) < 1800:
            return  # another worker is downloading
        os.remove(lock)
        return
    try:
        today = datetime.now(timezone.utc)
        months = [today.strftime("%Y-%m"), (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")]
        for m in months:
            url = f"https://download.db-ip.com/free/dbip-city-lite-{m}.mmdb.gz"
            try:
                tmp = GEO_FILE + ".part"
                with urllib.request.urlopen(url, timeout=120) as r, gzip.GzipFile(fileobj=r) as gz, open(tmp, "wb") as out:
                    shutil.copyfileobj(gz, out, 1 << 20)
                os.replace(tmp, GEO_FILE)
                app.logger.info("Geo database updated from %s", url)
                return
            except Exception as e:  # try last month's file
                app.logger.warning("Geo download failed for %s: %s", url, e)
    finally:
        os.close(fd)
        os.remove(lock)


def _geo_reader():
    try:
        mtime = os.path.getmtime(GEO_FILE)
    except OSError:
        mtime = 0
    if not mtime or time.time() - mtime > 32 * 86400:
        threading.Thread(target=_geo_download, daemon=True).start() if not os.path.exists(GEO_FILE + ".lock") else None
    if mtime and mtime != _geo["mtime"]:
        with _geo_lock:
            try:
                import maxminddb
                _geo["reader"], _geo["mtime"] = maxminddb.open_database(GEO_FILE), mtime
                geo_lookup.cache_clear()
            except Exception as e:
                app.logger.warning("Geo database not readable: %s", e)
    return _geo["reader"]


@lru_cache(maxsize=4096)
def geo_lookup(ip):
    reader = _geo["reader"]
    if not reader or not ip:
        return None, None
    try:
        rec = reader.get(ip) or {}
    except ValueError:
        return None, None
    name = lambda k: ((rec.get(k) or {}).get("names") or {}).get("en")
    return name("country"), name("city")


def client_ip():
    for h in ("CF-Connecting-IP", "True-Client-IP"):
        if request.headers.get(h):
            return request.headers[h].strip()
    xff = request.headers.get("X-Forwarded-For", "")
    return xff.split(",")[0].strip() if xff else (request.remote_addr or "")


def where():
    _geo_reader()
    country, city = geo_lookup(client_ip())
    if not country:
        cc = request.headers.get("CF-IPCountry", "")
        country = cc if re.fullmatch(r"[A-Z]{2}", cc) and cc not in ("XX", "T1") else None
    return country, city


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
    country, city = where()
    q("INSERT INTO access (at, email, page, ua, ip_hash, country, city) VALUES (?, ?, ?, ?, ?, ?, ?)",
      (now(), email, page, clip(request.user_agent.string, 300), ip_hash(), country, city))
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
    country, city = where()
    q("INSERT INTO visits (at, path, ref, vid, device, email, country, city) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
      (now(), path, ref, clip(d.get("vid"), 40), device, access_email(), country, city))
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
            return redirect("/admin/inbox")
        else:
            _fails[key].append(time.time())
            err = "That password is not right."
    return render_template_string(LOGIN_HTML, err=err)


@app.post("/admin/logout")
def admin_logout():
    session.clear()
    return redirect("/admin/login")


@app.get("/admin/export/<name>.csv")
def admin_export(name):
    r = admin_required()
    if r:
        return r
    cols = {"access": "at, email, page, country, city", "leads": "at, name, company, website, email, engagement, notes, page, access_email, status, admin_notes",
            "visits": "at, path, ref, vid, device, email, country, city"}
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
    if request.path not in INDEXED_PAGES:
        resp.headers.setdefault("X-Robots-Tag", "noindex")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    if request.path.startswith(("/admin", "/portal")):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------------------------------------------------------------- admin pages
from admin_ui import BASE_CSS, FONTS  # noqa: E402

LOGIN_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Automagic Admin</title><meta name="robots" content="noindex">""" + FONTS + """<style>""" + BASE_CSS + """
main{min-height:100vh;display:grid;place-items:center;padding:16px}
form{width:min(100%,380px);background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:28px}
label{display:block;margin:18px 0 8px}input{width:100%;font:16px Inter,sans-serif;color:var(--ink);background:var(--bg);border:1.5px solid var(--line);border-radius:10px;padding:12px}
input:focus{outline:none;border-color:var(--cyan)}button{width:100%;margin-top:16px;padding:13px}.err{color:#F2B8B5;font-size:14px;min-height:20px;margin:10px 0 0}
</style></head><body><main><form method="post"><p class="mono">Automagic</p><h1 style="margin:4px 0 0">Admin sign in</h1>
<label for="pw" class="mono">Password</label><input id="pw" name="password" type="password" autocomplete="current-password" required autofocus>
<p class="err" role="alert">{{ err }}</p><button type="submit">Sign in</button></form></main></body></html>"""

init_db()

import portal  # noqa: E402  (client portal + CRM; imports this module)
portal.init()
app.register_blueprint(portal.bp)
import admin  # noqa: E402  (left-menu admin: inbox, analytics, lead CRM, visitors)
admin.init()
app.register_blueprint(admin.bp)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=False)
