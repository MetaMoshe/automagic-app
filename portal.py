"""Client portal and admin CRM.

Admin (session["admin"]):
  /admin/clients            list, add, edit and delete clients (a simple CRM)
  /admin/clients/<id>       client record, portal login, Stripe link, files (drag and drop)
  /admin/consultants        consultants (add, edit, delete); each client is assigned one
                            and sees them under "Your Consultant"

Clients (session["client_id"]):
  /login/                   Client Login page (static, built from src/login.html)
  /portal/                  welcome, consultant, project docs, invoices, drive, settings

Files live on disk under $DATA_DIR/files/<client id>/ with random names; metadata is in
the client_files table. Invoices come from Stripe when STRIPE_SECRET_KEY is set and the
client has a Stripe customer id.
"""
import base64, json, mimetypes, os, secrets, sqlite3, time, urllib.error, urllib.parse, urllib.request
from collections import defaultdict

from flask import Blueprint, abort, jsonify, redirect, render_template_string, request, send_file, session
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

import admin_ui as U
import server as S

bp = Blueprint("portal", __name__)

FILES_DIR = os.path.join(S.DATA_DIR, "files")
MAX_FILE = 25 * 1024 * 1024
STRIPE_KEY = os.environ.get("STRIPE_SECRET_KEY", "").strip()
CAL_URL = "https://cal.com/moshe-david/ai-transformation-discovery?overlayCalendar=true"
STATUSES = ["Lead", "Introduction", "Active", "Paused", "Past"]
DOC_TYPES = ["Proposal", "Agreement", "Statement of work", "NDA", "Report", "Other"]
INLINE = {"application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp"}
CLIENT_FIELDS = ["company", "website", "industry", "country", "address", "status", "engagement",
                 "contact_name", "contact_title", "contact_email", "contact_phone", "contact_linkedin",
                 "notes", "stripe_customer_id", "phase", "consultant_note"]
PHASES = ["Introduction", "Discovery", "Design", "Build", "Adopt", "Run and evolve"]
PHASE_LINKS = {"Introduction": "", "Discovery": "#phases", "Design": "#phases", "Build": "#phases", "Adopt": "#phases", "Run and evolve": "#phases"}
PAGES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pages")
SELF_FIELDS = ["contact_name", "contact_title", "contact_phone", "contact_linkedin"]
CONSULTANT_DEFAULT = {"name": "Moshe David", "title": "Founder, Automagic AI", "email": "hello@automagic.agency",
                      "phone": "", "linkedin": "", "cal_url": CAL_URL,
                      "bio": "I lead every Automagic engagement personally, from discovery to the tools your team uses every day. "
                             "Reach out any time with questions, ideas or anything that isn't working.",
                      "photo": ""}
CON_FIELDS = ["name", "title", "email", "phone", "linkedin", "cal_url", "bio"]


# ---------------------------------------------------------------- database
def init():
    pk = "SERIAL PRIMARY KEY" if S.DATABASE_URL else "INTEGER PRIMARY KEY AUTOINCREMENT"
    cols = ", ".join(f"{c} TEXT" for c in CLIENT_FIELDS)
    with S.app.app_context():
        for sql in (
            f"CREATE TABLE IF NOT EXISTS clients (id {pk}, created TEXT NOT NULL, updated TEXT, {cols}, "
            "password_hash TEXT, portal_enabled INTEGER DEFAULT 0, session_ver INTEGER DEFAULT 1, last_login TEXT)",
            "CREATE UNIQUE INDEX IF NOT EXISTS clients_email ON clients (contact_email)",
            f"CREATE TABLE IF NOT EXISTS client_files (id {pk}, client_id INTEGER NOT NULL, at TEXT NOT NULL, kind TEXT NOT NULL, "
            "category TEXT, name TEXT NOT NULL, stored TEXT NOT NULL, size INTEGER, mime TEXT, uploaded_by TEXT)",
            "CREATE INDEX IF NOT EXISTS client_files_client ON client_files (client_id)",
            "CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT)",
            f"CREATE TABLE IF NOT EXISTS consultants (id {pk}, created TEXT NOT NULL, updated TEXT, "
            + ", ".join(f"{k} TEXT" for k in CON_FIELDS) + ", photo TEXT)",
            f"CREATE TABLE IF NOT EXISTS messages (id {pk}, client_id INTEGER NOT NULL, at TEXT NOT NULL, sender TEXT NOT NULL, "
            "author TEXT, subject TEXT, body TEXT NOT NULL, read_client TEXT, read_admin TEXT)",
            "CREATE INDEX IF NOT EXISTS messages_client ON messages (client_id)",
        ):
            S.q(sql)
        have = None if S.DATABASE_URL else [r[1] for r in S.q("PRAGMA table_info(clients)")]
        for col in CLIENT_FIELDS:
            if S.DATABASE_URL:
                S.q(f"ALTER TABLE clients ADD COLUMN IF NOT EXISTS {col} TEXT")
            elif col not in have:
                S.q(f"ALTER TABLE clients ADD COLUMN {col} TEXT")
        if S.DATABASE_URL:
            S.q("ALTER TABLE clients ADD COLUMN IF NOT EXISTS logo TEXT")
        elif "logo" not in have:
            S.q("ALTER TABLE clients ADD COLUMN logo TEXT")
        if S.DATABASE_URL:
            S.q("ALTER TABLE clients ADD COLUMN IF NOT EXISTS consultant_id INTEGER")
        elif "consultant_id" not in [r[1] for r in S.q("PRAGMA table_info(clients)")]:
            S.q("ALTER TABLE clients ADD COLUMN consultant_id INTEGER")
        if not S.q("SELECT id FROM consultants", one=True):
            # First run: carry over the single profile that used to live in settings.
            c = legacy_consultant()
            S.q(f"INSERT INTO consultants (created, updated, {', '.join(CON_FIELDS)}, photo) VALUES (?, ?, {', '.join('?' * len(CON_FIELDS))}, ?)",
                (S.now(), S.now(), *[c[k] for k in CON_FIELDS], c["photo"]))
        if not S.DATABASE_URL:
            S.db().commit()
    os.makedirs(FILES_DIR, exist_ok=True)


def commit():
    if not S.DATABASE_URL:
        S.db().commit()


def client_row(cid):
    r = S.q(f"SELECT id, created, updated, {', '.join(CLIENT_FIELDS)}, password_hash, portal_enabled, session_ver, last_login, "
            "consultant_id, logo FROM clients WHERE id = ?", (cid,), one=True)
    if not r:
        return None
    keys = ["id", "created", "updated"] + CLIENT_FIELDS + ["password_hash", "portal_enabled", "session_ver", "last_login", "consultant_id", "logo"]
    return dict(zip(keys, r))


def files_for(cid, kind=None):
    sql = "SELECT id, at, kind, category, name, size, mime, uploaded_by FROM client_files WHERE client_id = ?"
    args = [cid]
    if kind:
        sql += " AND kind = ?"
        args.append(kind)
    rows = S.q(sql + " ORDER BY at DESC, id DESC", tuple(args))
    return [dict(zip(["id", "at", "kind", "category", "name", "size", "mime", "uploaded_by"], r)) for r in rows]


MSG_KEYS = ["id", "at", "sender", "author", "subject", "body", "read_client", "read_admin"]


def messages_for(cid):
    rows = S.q("SELECT " + ", ".join(MSG_KEYS) + " FROM messages WHERE client_id = ? ORDER BY at, id", (cid,))
    return [dict(zip(MSG_KEYS, r)) for r in rows]


def add_message(cid, sender, author, subject, body):
    S.q("INSERT INTO messages (client_id, at, sender, author, subject, body) VALUES (?, ?, ?, ?, ?, ?)",
        (cid, S.now(), sender, author, subject, body))


def legacy_consultant():
    c = dict(CONSULTANT_DEFAULT)
    for k, v in S.q("SELECT k, v FROM settings WHERE k LIKE 'consultant.%'"):
        c[k.split(".", 1)[1]] = v or ""
    return c


CON_KEYS = ["id"] + CON_FIELDS + ["photo"]


def consultants():
    rows = S.q(f"SELECT id, {', '.join(CON_FIELDS)}, photo FROM consultants ORDER BY id")
    return [{k: (v if k == "id" else v or "") for k, v in zip(CON_KEYS, r)} for r in rows]


def consultant_row(pid):
    r = S.q(f"SELECT id, {', '.join(CON_FIELDS)}, photo FROM consultants WHERE id = ?", (pid,), one=True) if pid else None
    return {k: (v if k == "id" else v or "") for k, v in zip(CON_KEYS, r)} if r else None


def consultant_for(client):
    """The client's assigned consultant, else the first one (the default), else the built-in profile."""
    c = consultant_row(client.get("consultant_id")) if client else None
    if not c:
        r = S.q("SELECT id FROM consultants ORDER BY id", one=True)
        c = consultant_row(r[0]) if r else None
    return c or dict(CONSULTANT_DEFAULT, id=None)


def set_setting(k, v):
    if S.DATABASE_URL:
        S.q("INSERT INTO settings (k, v) VALUES (?, ?) ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v", (k, v))
    else:
        S.q("INSERT OR REPLACE INTO settings (k, v) VALUES (?, ?)", (k, v))


# The "Step" card on the portal overview: one set of guidance per project phase.
# {con} is replaced with the consultant's first name.
STAGE_CARD = {
    "Introduction": {
        "title": "Get to know how we work",
        "intro": "Before discovery begins, take ten minutes to see what the journey looks like: the five phases from discovery to running and evolving your AI, what we need from your team, and what you get at each step.",
        "steps": [["Learn how we work.", "Our approach, the five phases in detail and how we measure the return."],
                  ["See a real discovery report.", "The findings, solutions and roadmap we delivered for an executive search firm, so you know exactly what discovery produces."],
                  ["Bring your questions", "to your first session with {con}."]],
        "actions": [["How we work →", "/portal/how-we-work", 1, 0], ["See a real discovery report ↗", "/hunter/", 0, 1]],
    },
    "Discovery": {
        "title": "Discovery is under way",
        "intro": "Over 3–4 days we map how work flows today, where time and information are lost, and where AI creates the most leverage. It ends in a decision, not a pile of observations.",
        "steps": [["Book the interviews.", "60–90 minutes with key people across leadership and front-line teams."],
                  ["Share your materials.", "Representative workflows, templates and data exports, uploaded to Your Drive."],
                  ["Name a coordinator", "who can schedule access to people and systems."]],
        "actions": [["Upload to Your Drive →", "#drive", 1, 0], ["What discovery produces ↗", "/hunter/", 0, 1]],
    },
    "Design": {
        "title": "Designing your solution",
        "intro": "We're turning the roadmap into something you can approve: a solution design, a scoped set of tools and changes, a delivery plan in waves, and a business case with the KPIs it will be judged on.",
        "steps": [["Confirm your sponsor and product owner.", "They approve the design and own the results."],
                  ["Open doors to system owners", "so we can review integrations."],
                  ["Check the business case assumptions.", "Your figures make the ROI model real."]],
        "actions": [["Review project docs →", "#legal", 1, 0], ["Try the ROI calculator", "#roi", 0, 0]],
    },
    "Build": {
        "title": "Building in waves",
        "intro": "We deliver in short waves so your team uses real tools early. Each wave is designed, built, tested and launched inside your existing systems, then reviewed before the next one starts.",
        "steps": [["Make your experts available", "for each wave's design and testing."],
                  ["Keep access to systems and data flowing", "so the build doesn't stall."],
                  ["Sign off each wave.", "We review adoption, time saved and issues, then agree go or no-go for the next."]],
        "actions": [["Message {con} →", "#inbox", 1, 0], ["The build phase in detail", "/portal/how-we-work#phases", 0, 0]],
    },
    "Adopt": {
        "title": "Getting your team using it",
        "intro": "Tools that are built but not used return nothing. Hands-on training, office hours and team champions make sure every launch turns into daily use.",
        "steps": [["Join the hands-on sessions.", "90 minutes per team, per wave."],
                  ["Use weekly office hours", "for the four weeks after each launch."],
                  ["Share feedback inside the tools.", "It becomes the improvement backlog."]],
        "actions": [["Book office hours →", "{cal}", 1, 1], ["Send feedback", "#inbox", 0, 0]],
    },
    "Run and evolve": {
        "title": "Running and evolving",
        "intro": "Everything we delivered is monitored, tuned and extended. The data and results it produces shape what comes next.",
        "steps": [["Join the monthly steering meeting", "for usage, costs and fixes."],
                  ["Review the return each quarter", "against the KPIs agreed in Design."],
                  ["Tell us what's next.", "New opportunities found in real use become the next stage."]],
        "actions": [["Book a review →", "{cal}", 1, 1], ["Message {con}", "#inbox", 0, 0]],
    },
}


def stage_card(phase, con):
    d = STAGE_CARD.get(phase) or STAGE_CARD["Introduction"]
    first = (con.get("name") or "your consultant").split(" ")[0]
    f = lambda x: x.replace("{con}", first).replace("{cal}", con.get("cal_url") or "#consultant")
    return {"title": d["title"], "intro": f(d["intro"]), "steps": [[f(a), f(b)] for a, b in d["steps"]],
            "actions": [[f(l), f(h), pr, bool(ext) and not f(h).startswith("#")] for l, h, pr, ext in d["actions"]]}


PRICING_DEFAULT = {
    "label": "Engagement models & pricing",
    "heading": "How we charge",
    "intro": "A hybrid model, so you pay separately for leadership, ongoing expertise and what is built, each on its own terms. "
             "Prices below are starting points; your exact fees are set out in your proposal.",
    "principles": [
        {"title": "You own everything we build.", "text": "Code, prompts, workflows and data stay yours."},
        {"title": "No mark-up on external costs.", "text": "You pay providers directly, at cost."},
        {"title": "Priced before work starts.", "text": "Each wave is agreed before it begins, with no surprises."},
    ],
    "items": [
        {"title": "Discovery", "badge": "One-off · fixed", "prefix": "Starting from", "price": "$XXXX", "unit": "fixed",
         "text": "A 3–4 day investigation, your AI Transformation Report and a leadership readout.",
         "link_label": "See a real discovery report", "link_url": "/hunter/", "outline": False},
        {"title": "Fractional Transformation Lead", "badge": "Monthly", "prefix": "Starting from", "price": "$XXXX", "unit": "a month",
         "text": "A senior lead for your AI programme: roadmap, governance rules, vendors, adoption and monthly steering reports.",
         "link_label": "", "link_url": "", "outline": False},
        {"title": "Monthly consultancy", "badge": "Monthly", "prefix": "Starting from", "price": "$XXXX", "unit": "a month",
         "text": "Architecture, delivery management, monitoring, fixes, minor changes and tuning.",
         "link_label": "", "link_url": "", "outline": False},
        {"title": "Build: fixed cost per item", "badge": "Fixed per item", "prefix": "", "price": "", "unit": "",
         "text": "Design, build, integration, testing, onboarding and launch of each agent, tool or change, priced before its wave starts.",
         "link_label": "", "link_url": "", "outline": False},
        {"title": "External costs", "badge": "Paid by you · at cost", "prefix": "", "price": "", "unit": "",
         "text": "Software licences, AI model usage, hosting, data providers and APIs, billed to you directly by each provider.",
         "link_label": "", "link_url": "", "outline": True},
    ],
    "flow_title": "How it fits together",
    "flow": [
        {"title": "Discovery", "text": "Once, at the start"},
        {"title": "Lead + consultancy", "text": "Monthly, while the programme runs"},
        {"title": "Builds in waves", "text": "A fixed price per item, agreed per wave"},
        {"title": "External costs", "text": "Direct to providers, as you use them"},
    ],
    "note": "Your exact fees are in your proposal.",
}
PRICING_SLOTS = {"principles": 4, "items": 8, "flow": 6}
PRICING_ITEM_KEYS = ["title", "badge", "prefix", "price", "unit", "text", "link_label", "link_url"]


def pricing_content():
    """Pricing page content: the saved JSON from the admin CMS, or the defaults."""
    r = S.q("SELECT v FROM settings WHERE k = 'pricing.page'", one=True)
    data = json.loads(json.dumps(PRICING_DEFAULT))
    if r and r[0]:
        try:
            saved = json.loads(r[0])
            data.update({k: v for k, v in saved.items() if k in data})
        except ValueError:
            pass
    return data


def safe_link(u):
    u = (u or "").strip()
    return u if u.startswith(("https://", "http://", "mailto:")) or (u.startswith("/") and not u.startswith("//")) else ""


# ---------------------------------------------------------------- request guards
@bp.before_app_request
def guards():
    p = request.path
    if request.method == "POST" and (p.startswith("/admin") or p.startswith("/portal")):
        origin = request.headers.get("Origin")
        if origin and urllib.parse.urlparse(origin).netloc != request.host:
            abort(403)
    uploads = p.startswith("/admin/clients/") and p.endswith("/files") or p == "/portal/api/drive" or p.startswith("/admin/consultants/") or (p.startswith("/admin/clients/") and p.endswith("/logo"))
    limit = MAX_FILE + 1024 * 1024 if uploads else 64 * 1024
    if (request.content_length or 0) > limit:
        abort(413)


def admin_only():
    r = S.admin_required()
    if r:
        if request.path.startswith("/admin/") and request.method == "POST":
            abort(401)
        return r
    return None


def current_client():
    cid, ver = session.get("client_id"), session.get("client_ver")
    if not cid:
        return None
    c = client_row(cid)
    if not c or not c["portal_enabled"] or c["session_ver"] != ver:
        session.pop("client_id", None)
        return None
    return c


# ---------------------------------------------------------------- files
def save_upload(cid, f, kind, category, by):
    name = (f.filename or "file").replace("\\", "/").rsplit("/", 1)[-1][:200] or "file"
    data = f.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        return None, f"{name} is larger than 25 MB."
    if not data:
        return None, f"{name} is empty."
    ext = os.path.splitext(secure_filename(name))[1][:12].lower()
    stored = secrets.token_hex(16) + ext
    folder = os.path.join(FILES_DIR, str(int(cid)))
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, stored), "wb") as out:
        out.write(data)
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    S.q("INSERT INTO client_files (client_id, at, kind, category, name, stored, size, mime, uploaded_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (cid, S.now(), kind, category, name, stored, len(data), mime, by))
    commit()
    return name, None


def send_stored(cid, fid):
    r = S.q("SELECT name, stored, mime FROM client_files WHERE id = ? AND client_id = ?", (fid, cid), one=True)
    if not r:
        abort(404)
    path = os.path.join(FILES_DIR, str(int(cid)), r[1])
    if not os.path.isfile(path):
        abort(404)
    inline = r[2] in INLINE and request.args.get("dl") != "1"
    resp = send_file(path, mimetype=r[2] if inline else "application/octet-stream", as_attachment=not inline,
                     download_name=r[0], max_age=0)
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


def delete_stored(cid, fid):
    r = S.q("SELECT stored FROM client_files WHERE id = ? AND client_id = ?", (fid, cid), one=True)
    if not r:
        return False
    try:
        os.remove(os.path.join(FILES_DIR, str(int(cid)), r[0]))
    except OSError:
        pass
    S.q("DELETE FROM client_files WHERE id = ? AND client_id = ?", (fid, cid))
    commit()
    return True


# ---------------------------------------------------------------- Stripe
def stripe(method, path, params=None):
    if not STRIPE_KEY:
        raise RuntimeError("Stripe is not connected.")
    data = urllib.parse.urlencode(params or {}, doseq=True).encode() if method == "POST" else None
    url = "https://api.stripe.com/v1/" + path
    if method == "GET" and params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Basic " + base64.b64encode((STRIPE_KEY + ":").encode()).decode()})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error", {}).get("message")
        except Exception:
            msg = None
        raise RuntimeError(msg or f"Stripe returned {e.code}.")


_inv_cache = {}


def invoices(customer):
    if not (STRIPE_KEY and customer):
        return None, None
    hit = _inv_cache.get(customer)
    if hit and hit[0] > time.time() - 60:
        return hit[1], None
    try:
        data = stripe("GET", "invoices", {"customer": customer, "limit": 30})
    except Exception as e:
        return [], str(e)
    out = []
    for i in data.get("data", []):
        if i.get("status") == "draft":
            continue
        out.append({"number": i.get("number") or i.get("id"), "created": time.strftime("%d %b %Y", time.gmtime(i.get("created", 0))),
                    "due": time.strftime("%d %b %Y", time.gmtime(i["due_date"])) if i.get("due_date") else "",
                    "amount": f"{(i.get('total') or 0) / 100:,.2f} {str(i.get('currency', '')).upper()}",
                    "due_amount": i.get("amount_remaining") or 0, "status": i.get("status") or "",
                    "url": i.get("hosted_invoice_url") or "", "pdf": i.get("invoice_pdf") or ""})
    _inv_cache[customer] = (time.time(), out)
    return out, None


# ---------------------------------------------------------------- admin CRM
@bp.get("/admin/clients")
def admin_clients():
    r = admin_only()
    if r:
        return r
    rows = S.q("SELECT c.id, c.company, c.contact_name, c.contact_email, c.status, c.engagement, c.portal_enabled, c.last_login, "
               "(SELECT COUNT(*) FROM client_files f WHERE f.client_id = c.id), c.consultant_id FROM clients c ORDER BY LOWER(c.company)")
    cons = consultants()
    names = {x["id"]: x["name"] for x in cons}
    default = cons[0]["name"] if cons else CONSULTANT_DEFAULT["name"]
    rows = [(*row[:9], names.get(row[9]) or default) for row in rows]
    counts = defaultdict(int)
    for row in rows:
        counts[row[4] or "Lead"] += 1
    return render_template_string(ADMIN_LIST, rows=rows, counts=counts, statuses=STATUSES, stripe=bool(STRIPE_KEY))


@bp.route("/admin/clients/new", methods=["GET", "POST"])
@bp.route("/admin/clients/<int:cid>", methods=["GET", "POST"])
def admin_client(cid=None):
    r = admin_only()
    if r:
        return r
    c = client_row(cid) if cid else {k: "" for k in ["id"] + CLIENT_FIELDS} | {"status": "Active", "portal_enabled": 0, "password_hash": None, "consultant_id": None}
    if cid and not c:
        abort(404)
    err, note, new_pw = "", request.args.get("saved") and "Saved.", ""
    if request.method == "POST":
        f = request.form
        vals = {k: S.clip(f.get(k), 4000 if k in ("notes", "address", "consultant_note") else 300) for k in CLIENT_FIELDS}
        vals["phase"] = vals["phase"] if vals["phase"] in PHASES else ""
        vals["contact_email"] = vals["contact_email"].lower()
        vals["status"] = vals["status"] if vals["status"] in STATUSES else "Active"
        vals["engagement"] = vals["engagement"] if vals["engagement"] in S.TYPES else ""
        portal = 1 if f.get("portal_enabled") else 0
        con_id = int(f["consultant_id"]) if (f.get("consultant_id") or "").isdigit() else None
        con_id = con_id if consultant_row(con_id) else None
        pw = f.get("password", "").strip()
        if f.get("generate"):
            pw = new_pw = "-".join(secrets.token_urlsafe(4) for _ in range(3))
        c.update(vals)
        c["portal_enabled"] = portal
        c["consultant_id"] = con_id
        if not vals["company"]:
            err = "Add the company name."
        elif vals["contact_email"] and not S.EMAIL_RE.match(vals["contact_email"]):
            err = "The contact email doesn't look right."
        elif portal and not vals["contact_email"]:
            err = "Portal access needs a contact email, which is the client's login."
        elif pw and len(pw) < 10:
            err = "Use a password of at least 10 characters."
        elif portal and not pw and not c.get("password_hash"):
            err = "Set a password (or generate one) to turn on portal access."
        else:
            dup = S.q("SELECT id FROM clients WHERE contact_email = ? AND id != ?", (vals["contact_email"], cid or 0), one=True) if vals["contact_email"] else None
            if dup:
                err = "Another client already uses that contact email."
        if not err:
            sets = ", ".join(f"{k} = ?" for k in CLIENT_FIELDS)
            args = [vals[k] or None if k == "contact_email" else vals[k] for k in CLIENT_FIELDS]
            if cid:
                S.q(f"UPDATE clients SET {sets}, updated = ?, portal_enabled = ?, consultant_id = ? WHERE id = ?", (*args, S.now(), portal, con_id, cid))
            else:
                S.q(f"INSERT INTO clients (created, updated, {', '.join(CLIENT_FIELDS)}, portal_enabled, consultant_id) VALUES (?, ?, {', '.join('?' * len(CLIENT_FIELDS))}, ?, ?)",
                    (S.now(), S.now(), *args, portal, con_id))
                cid = S.q("SELECT id FROM clients WHERE company = ? ORDER BY id DESC", (vals["company"],), one=True)[0]
            if pw:
                S.q("UPDATE clients SET password_hash = ?, session_ver = COALESCE(session_ver, 1) + 1 WHERE id = ?",
                    (generate_password_hash(pw), cid))
            commit()
            if new_pw:
                session["new_pw"] = [cid, new_pw]
            return redirect(f"/admin/clients/{cid}?saved=1")
    shown = session.pop("new_pw", None) if request.method == "GET" else None
    if shown and shown[0] == cid:
        new_pw, note = shown[1], "Saved. Send the client their login below; this password is shown only once."
    files = files_for(cid) if cid else []
    inv, inv_err = invoices(c.get("stripe_customer_id")) if cid else (None, None)
    return render_template_string(ADMIN_CLIENT, c=c, cid=cid, err=err, note=note, new_pw=new_pw, files=files,
                                  statuses=STATUSES, types=S.TYPES, phases=PHASES, doc_types=DOC_TYPES, stripe=bool(STRIPE_KEY),
                                  inv=inv, inv_err=inv_err, login_url=request.host_url.rstrip("/") + "/login/", cons=consultants())


@bp.post("/admin/clients/<int:cid>/delete")
def admin_client_delete(cid):
    r = admin_only()
    if r:
        return r
    if request.form.get("confirm") != "DELETE":
        return redirect(f"/admin/clients/{cid}")
    for f in files_for(cid):
        delete_stored(cid, f["id"])
    try:
        os.rmdir(os.path.join(FILES_DIR, str(int(cid))))
    except OSError:
        pass
    S.q("DELETE FROM messages WHERE client_id = ?", (cid,))
    S.q("DELETE FROM clients WHERE id = ?", (cid,))
    commit()
    return redirect("/admin/clients")


@bp.post("/admin/clients/<int:cid>/stripe")
def admin_client_stripe(cid):
    r = admin_only()
    if r:
        return r
    c = client_row(cid) or abort(404)
    try:
        cust = stripe("POST", "customers", {"name": c["company"], "email": c["contact_email"] or "",
                                            "metadata[automagic_client_id]": str(cid)})
        S.q("UPDATE clients SET stripe_customer_id = ? WHERE id = ?", (cust["id"], cid))
        commit()
    except Exception as e:
        return redirect(f"/admin/clients/{cid}?stripe_error=" + urllib.parse.quote(str(e)))
    return redirect(f"/admin/clients/{cid}?saved=1")


@bp.post("/admin/clients/<int:cid>/files")
def admin_upload(cid):
    r = admin_only()
    if r:
        return r
    if not client_row(cid):
        abort(404)
    kind = "legal" if request.form.get("kind") == "legal" else "drive"
    cat = request.form.get("category") if request.form.get("category") in DOC_TYPES else ("Other" if kind == "legal" else None)
    saved, errors = [], []
    for f in request.files.getlist("file"):
        name, e = save_upload(cid, f, kind, cat, "admin")
        (saved if name else errors).append(name or e)
    return jsonify(ok=not errors, saved=saved, errors=errors)


LOGO_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/svg+xml": ".svg"}


def logo_response(c):
    name = os.path.basename((c or {}).get("logo") or "")
    path = os.path.join(FILES_DIR, str(int(c["id"])), name) if name else ""
    if not name or not os.path.isfile(path):
        abort(404)
    resp = send_file(path, max_age=300)
    # SVG can carry script: never let it run if the file is opened directly.
    resp.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
    return resp


@bp.post("/admin/clients/<int:cid>/logo")
def admin_client_logo(cid):
    r = admin_only()
    if r:
        return r
    c = client_row(cid)
    if not c:
        abort(404)
    folder = os.path.join(FILES_DIR, str(int(cid)))
    old = os.path.basename(c.get("logo") or "")
    if request.form.get("remove"):
        name, msg = None, "Logo removed."
    else:
        f = request.files.get("logo")
        mime = mimetypes.guess_type(f.filename)[0] if f and f.filename else None
        data = f.read(2 * 1024 * 1024 + 1) if f else b""
        if mime not in LOGO_TYPES or not 0 < len(data) <= 2 * 1024 * 1024:
            return redirect(f"/admin/clients/{cid}?logo_error=1")
        os.makedirs(folder, exist_ok=True)
        name = "logo-" + secrets.token_hex(8) + LOGO_TYPES[mime]
        with open(os.path.join(folder, name), "wb") as out:
            out.write(data)
        msg = "Logo saved."
    S.q("UPDATE clients SET logo = ?, updated = ? WHERE id = ?", (name, S.now(), cid))
    commit()
    if old:
        try:
            os.remove(os.path.join(folder, old))
        except OSError:
            pass
    return redirect(f"/admin/clients/{cid}?saved=1&logo_note=" + urllib.parse.quote(msg))


@bp.get("/admin/clients/<int:cid>/logo")
def admin_client_logo_get(cid):
    r = admin_only()
    if r:
        return r
    return logo_response(client_row(cid))


@bp.get("/portal/how-we-work")
def portal_how_we_work():
    if not current_client() and not session.get("admin"):
        return redirect("/login/")
    resp = send_file(os.path.join(PAGES_DIR, "how-we-work.html"), mimetype="text/html", max_age=0)
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@bp.get("/portal/logo")
def portal_logo():
    c = current_client()
    if not c:
        abort(404)
    return logo_response(c)


@bp.get("/admin/clients/<int:cid>/files/<int:fid>")
def admin_file(cid, fid):
    r = admin_only()
    if r:
        return r
    return send_stored(cid, fid)


@bp.post("/admin/clients/<int:cid>/files/<int:fid>/delete")
def admin_file_delete(cid, fid):
    r = admin_only()
    if r:
        return r
    return jsonify(ok=delete_stored(cid, fid))


@bp.get("/admin/consultant")
def admin_consultant_old():
    return redirect("/admin/consultants")


@bp.get("/admin/consultants")
def admin_consultants():
    r = admin_only()
    if r:
        return r
    counts = dict(S.q("SELECT consultant_id, COUNT(*) FROM clients WHERE consultant_id IS NOT NULL GROUP BY consultant_id"))
    unassigned = S.q("SELECT COUNT(*) FROM clients WHERE consultant_id IS NULL", one=True)[0]
    cons = consultants()
    if cons:
        counts[cons[0]["id"]] = counts.get(cons[0]["id"], 0) + unassigned
    return render_template_string(ADMIN_CONSULTANTS, cons=cons, counts=counts, note="Deleted." if request.args.get("deleted") else "")


def save_photo(photo, old):
    """Store an uploaded consultant photo; return the new file name, or None if it isn't a valid image."""
    mime = mimetypes.guess_type(photo.filename)[0]
    data = photo.read(5 * 1024 * 1024 + 1)
    if mime not in ("image/png", "image/jpeg", "image/webp") or not 0 < len(data) <= 5 * 1024 * 1024:
        return None
    folder = os.path.join(FILES_DIR, "consultant")
    os.makedirs(folder, exist_ok=True)
    name = secrets.token_hex(8) + mimetypes.guess_extension(mime).replace(".jpe", ".jpg")
    with open(os.path.join(folder, name), "wb") as out:
        out.write(data)
    remove_photo(old)
    return name


def remove_photo(name):
    if name:
        try:
            os.remove(os.path.join(FILES_DIR, "consultant", os.path.basename(name)))
        except OSError:
            pass


@bp.route("/admin/consultants/new", methods=["GET", "POST"])
@bp.route("/admin/consultants/<int:pid>", methods=["GET", "POST"])
def admin_consultant(pid=None):
    r = admin_only()
    if r:
        return r
    c = consultant_row(pid) if pid else {k: "" for k in CON_KEYS} | {"id": None, "cal_url": CAL_URL}
    if pid and not c:
        abort(404)
    err, note = "", "Saved." if request.args.get("saved") else ""
    if request.method == "POST":
        vals = {k: S.clip(request.form.get(k), 3000 if k == "bio" else 300) for k in CON_FIELDS}
        vals["email"] = vals["email"].lower()
        c.update(vals)
        if not vals["name"]:
            err = "Add the consultant's name."
        elif vals["email"] and not S.EMAIL_RE.match(vals["email"]):
            err = "The email doesn't look right."
        else:
            sets = ", ".join(f"{k} = ?" for k in CON_FIELDS)
            if pid:
                S.q(f"UPDATE consultants SET {sets}, updated = ? WHERE id = ?", (*[vals[k] for k in CON_FIELDS], S.now(), pid))
            else:
                S.q(f"INSERT INTO consultants (created, updated, {', '.join(CON_FIELDS)}, photo) VALUES (?, ?, {', '.join('?' * len(CON_FIELDS))}, '')",
                    (S.now(), S.now(), *[vals[k] for k in CON_FIELDS]))
                pid = S.q("SELECT id FROM consultants ORDER BY id DESC", one=True)[0]
            photo = request.files.get("photo")
            bad_photo = False
            if photo and photo.filename:
                name = save_photo(photo, c.get("photo"))
                if name:
                    S.q("UPDATE consultants SET photo = ? WHERE id = ?", (name, pid))
                else:
                    bad_photo = True
            commit()
            return redirect(f"/admin/consultants/{pid}?saved=1" + ("&photo=bad" if bad_photo else ""))
    if request.args.get("photo") == "bad":
        note = "Saved, but the photo wasn't: use a PNG, JPG or WebP under 5 MB."
    first = S.q("SELECT id FROM consultants ORDER BY id", one=True)
    nclients = S.q("SELECT COUNT(*) FROM clients WHERE consultant_id = ?", (pid,), one=True)[0] if pid else 0
    return render_template_string(ADMIN_CONSULTANT, c=c, pid=pid, err=err, note=note, nclients=nclients,
                                  is_default=bool(pid and first and first[0] == pid))


@bp.route("/admin/clients/<int:cid>/inbox", methods=["GET", "POST"])
def admin_client_inbox(cid):
    r = admin_only()
    if r:
        return r
    c = client_row(cid)
    if not c:
        abort(404)
    con = consultant_for(c)
    if request.method == "POST":
        body = S.clip(request.form.get("body"), 10000).strip()
        if not body:
            return redirect(f"/admin/clients/{cid}/inbox?err=1")
        author = S.clip(request.form.get("author"), 200) or con["name"]
        add_message(cid, "consultant", author, S.clip(request.form.get("subject"), 200), body)
        commit()
        return redirect(f"/admin/clients/{cid}/inbox?sent=1#latest")
    msgs = messages_for(cid)
    unread_ids = [m["id"] for m in msgs if m["sender"] == "client" and not m["read_admin"]]
    if unread_ids:
        S.q("UPDATE messages SET read_admin = ? WHERE client_id = ? AND sender = 'client' AND read_admin IS NULL", (S.now(), cid))
        commit()
    return render_template_string(ADMIN_INBOX, c=c, cid=cid, con=con, msgs=msgs, new_ids=unread_ids,
                                  sent=request.args.get("sent"), err=request.args.get("err"))


@bp.route("/admin/pricing", methods=["GET", "POST"])
def admin_pricing():
    r = admin_only()
    if r:
        return r
    f = request.form
    if request.method == "POST":
        if f.get("reset") == "1":
            S.q("DELETE FROM settings WHERE k = 'pricing.page'")
            commit()
            return redirect("/admin/pricing?saved=reset")
        data = {k: S.clip(f.get(k), 1000 if k in ("intro", "note") else 200) for k in ("label", "heading", "intro", "flow_title", "note")}
        data["label"] = data["label"] or PRICING_DEFAULT["label"]
        data["heading"] = data["heading"] or PRICING_DEFAULT["heading"]
        data["principles"] = [{"title": S.clip(f.get(f"pr{i}_title"), 200), "text": S.clip(f.get(f"pr{i}_text"), 400)}
                              for i in range(PRICING_SLOTS["principles"])]
        data["items"] = []
        for i in range(PRICING_SLOTS["items"]):
            it = {k: S.clip(f.get(f"it{i}_{k}"), 600 if k == "text" else 200) for k in PRICING_ITEM_KEYS}
            it["link_url"] = safe_link(it["link_url"])
            it["outline"] = f.get(f"it{i}_outline") == "1"
            data["items"].append(it)
        data["items"].sort(key=lambda x: not x["title"])  # filled cards first
        data["flow"] = [{"title": S.clip(f.get(f"fl{i}_title"), 120), "text": S.clip(f.get(f"fl{i}_text"), 200)}
                        for i in range(PRICING_SLOTS["flow"])]
        set_setting("pricing.page", json.dumps(data))
        commit()
        return redirect("/admin/pricing?saved=1")
    pr = pricing_content()
    for k, n in PRICING_SLOTS.items():  # pad with empty slots so new cards can be added
        blank = {"outline": False, **{x: "" for x in PRICING_ITEM_KEYS}} if k == "items" else {"title": "", "text": ""}
        pr[k] = (pr[k] + [dict(blank) for _ in range(n)])[:n]
    saved = request.args.get("saved")
    note = "Saved. Clients see the changes straight away." if saved == "1" else "Reset to the default content." if saved == "reset" else ""
    custom = bool(S.q("SELECT 1 FROM settings WHERE k = 'pricing.page'", one=True))
    return render_template_string(ADMIN_PRICING, pr=pr, note=note, custom=custom)


@bp.post("/admin/consultants/<int:pid>/delete")
def admin_consultant_delete(pid):
    r = admin_only()
    if r:
        return r
    c = consultant_row(pid)
    if not c:
        abort(404)
    if request.form.get("confirm") != "DELETE":
        return redirect(f"/admin/consultants/{pid}")
    S.q("UPDATE clients SET consultant_id = NULL WHERE consultant_id = ?", (pid,))
    S.q("DELETE FROM consultants WHERE id = ?", (pid,))
    commit()
    remove_photo(c.get("photo"))
    return redirect("/admin/consultants?deleted=1")


@bp.get("/consultant-photo/<int:pid>")
def consultant_photo(pid):
    if session.get("admin"):
        c = consultant_row(pid)
    else:
        client = current_client()
        c = consultant_for(client) if client else None
        if not c or c.get("id") != pid:
            abort(404)
    name = os.path.basename((c or {}).get("photo") or "")
    path = os.path.join(FILES_DIR, "consultant", name)
    if not name or not os.path.isfile(path):
        abort(404)
    return send_file(path, max_age=300)


# ---------------------------------------------------------------- client login
_fails = defaultdict(list)


@bp.post("/portal/api/login")
def client_login():
    d = request.get_json(silent=True) or {}
    email, pw = S.clip(d.get("email"), 254).lower(), str(d.get("password") or "")[:200]
    key = S.ip_hash()
    _fails[key] = [t for t in _fails[key] if t > time.time() - 900]
    if len(_fails[key]) >= 8:
        return jsonify(ok=False, error="Too many attempts. Try again in 15 minutes."), 429
    r = S.q("SELECT id, password_hash, portal_enabled, session_ver FROM clients WHERE contact_email = ?", (email,), one=True) if email else None
    if not r or not r[1] or not r[2] or not check_password_hash(r[1], pw):
        _fails[key].append(time.time())
        return jsonify(ok=False, error="That email and password don't match an active client login."), 401
    session.clear()
    session.permanent = True
    session["client_id"], session["client_ver"] = r[0], r[3]
    S.q("UPDATE clients SET last_login = ? WHERE id = ?", (S.now(), r[0]))
    commit()
    return jsonify(ok=True, next="/portal/")


@bp.post("/portal/logout")
def client_logout():
    session.pop("client_id", None)
    session.pop("client_ver", None)
    return redirect("/login/")


# ---------------------------------------------------------------- client portal
@bp.get("/portal")
@bp.get("/portal/")
def portal():
    c = current_client()
    if not c:
        return redirect("/login/")
    inv, inv_err = invoices(c["stripe_customer_id"])
    first = (c["contact_name"] or "").split(" ")[0] or c["company"]
    msgs = messages_for(c["id"])
    return render_template_string(PORTAL, c=c, first=first, pr=pricing_content(), stage=stage_card(c.get("phase") if c.get("phase") in PHASES else "Introduction", consultant_for(c)), msgs=msgs, unread=sum(1 for m in msgs if m["sender"] == "consultant" and not m["read_client"]), phases=PHASES, phase_links=PHASE_LINKS, con=consultant_for(c), legal=files_for(c["id"], "legal"),
                                  drive=files_for(c["id"], "drive"), inv=inv, inv_err=inv_err, stripe=bool(STRIPE_KEY),
                                  note=request.args.get("note", "")[:200])


@bp.get("/portal/files/<int:fid>")
def portal_file(fid):
    c = current_client()
    if not c:
        abort(404)
    return send_stored(c["id"], fid)


@bp.post("/portal/api/drive")
def portal_upload():
    c = current_client()
    if not c:
        abort(401)
    saved, errors = [], []
    for f in request.files.getlist("file"):
        name, e = save_upload(c["id"], f, "drive", None, "client")
        (saved if name else errors).append(name or e)
    return jsonify(ok=not errors, saved=saved, errors=errors)


@bp.post("/portal/api/drive/<int:fid>/delete")
def portal_delete(fid):
    c = current_client()
    if not c:
        abort(401)
    r = S.q("SELECT id FROM client_files WHERE id = ? AND client_id = ? AND kind = 'drive' AND uploaded_by = 'client'",
            (fid, c["id"]), one=True)
    return jsonify(ok=bool(r) and delete_stored(c["id"], fid))


@bp.post("/portal/billing")
def portal_billing():
    c = current_client()
    if not c:
        return redirect("/login/")
    try:
        s = stripe("POST", "billing_portal/sessions", {"customer": c["stripe_customer_id"],
                                                      "return_url": request.host_url.rstrip("/") + "/portal/#invoices"})
        return redirect(s["url"])
    except Exception as e:
        return redirect("/portal/?note=" + urllib.parse.quote("Billing settings are not available right now: " + str(e)) + "#invoices")


@bp.post("/portal/inbox")
def portal_inbox_send():
    c = current_client()
    if not c:
        abort(401)
    body = S.clip(request.form.get("body"), 10000).strip()
    if not body:
        return redirect("/portal/?note=Write+a+message+first.#inbox")
    add_message(c["id"], "client", c["contact_name"] or c["contact_email"], S.clip(request.form.get("subject"), 200), body)
    commit()
    return redirect("/portal/?note=Message+sent.#inbox")


@bp.post("/portal/api/inbox/read")
def portal_inbox_read():
    c = current_client()
    if not c:
        abort(401)
    S.q("UPDATE messages SET read_client = ? WHERE client_id = ? AND sender = 'consultant' AND read_client IS NULL", (S.now(), c["id"]))
    commit()
    return jsonify(ok=True)


@bp.post("/portal/settings")
def portal_settings():
    c = current_client()
    if not c:
        return redirect("/login/")
    f = request.form
    if f.get("action") == "profile":
        vals = [S.clip(f.get(k), 300) for k in SELF_FIELDS]
        S.q(f"UPDATE clients SET {', '.join(k + ' = ?' for k in SELF_FIELDS)}, updated = ? WHERE id = ?", (*vals, S.now(), c["id"]))
        commit()
        return redirect("/portal/?note=Profile+saved.#settings")
    if f.get("action") == "password":
        if not check_password_hash(c["password_hash"] or "", f.get("current", "")):
            return redirect("/portal/?note=Your+current+password+is+not+right.#settings")
        new = f.get("new", "")
        if len(new) < 10 or new != f.get("confirm", ""):
            return redirect("/portal/?note=New+passwords+must+match+and+be+at+least+10+characters.#settings")
        S.q("UPDATE clients SET password_hash = ?, session_ver = session_ver + 1 WHERE id = ?", (generate_password_hash(new), c["id"]))
        commit()
        session["client_ver"] = c["session_ver"] + 1
        return redirect("/portal/?note=Password+changed.+Other+devices+are+signed+out.#settings")
    if f.get("action") == "signout_all":
        S.q("UPDATE clients SET session_ver = session_ver + 1 WHERE id = ?", (c["id"],))
        commit()
        session["client_ver"] = c["session_ver"] + 1
        return redirect("/portal/?note=Signed+out+of+all+other+devices.#settings")
    return redirect("/portal/#settings")


# ---------------------------------------------------------------- templates
def size(n):
    n = n or 0
    return f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{max(1, round(n / 1024))} KB"


def ext(name):
    e = os.path.splitext(name or "")[1][1:].upper()
    return e[:4] or "FILE"


S.app.jinja_env.filters["fsize"] = size
S.app.jinja_env.filters["ext"] = ext

ADMIN_FOOT = U.FOOT

ADMIN_LIST = U.head("clients", "Clients") + """
<section><div class="row" style="justify-content:space-between"><h2 style="margin:0">Clients</h2><a class="btn" href="/admin/clients/new">Add client</a></div>
<p class="mono" style="margin:10px 0 16px;text-transform:none;letter-spacing:0">{% for s in statuses %}{{ s }}: {{ counts[s] }}{% if not loop.last %} · {% endif %}{% endfor %}{% if not stripe %} · Stripe not connected (set STRIPE_SECRET_KEY){% endif %}</p>
{% if rows %}<div class="tw"><table><thead><tr><th>Company</th><th>Contact</th><th>Status</th><th>Engagement</th><th>Consultant</th><th>Portal</th><th>Files</th><th>Last login (UTC)</th></tr></thead><tbody>
{% for id, company, name, email, status, eng, portal, last, nfiles, con in rows %}<tr class="rowlink" onclick="location='/admin/clients/{{ id }}'"><td><a href="/admin/clients/{{ id }}">{{ company }}</a>{% set n = client_unread(id) %}{% if n %} <a class="pill" href="/admin/clients/{{ id }}/inbox" title="Unread messages">{{ n }} new message{{ '' if n == 1 else 's' }}</a>{% endif %}</td><td>{{ name or '' }}{% if email %}<br><span class="mono" style="text-transform:none;letter-spacing:0">{{ email }}</span>{% endif %}</td>
<td><span class="pill{% if status != 'Active' %} off{% endif %}">{{ status or 'Lead' }}</span></td><td>{{ eng or '' }}</td><td>{{ con }}</td><td>{% if portal %}<span class="pill">On</span>{% else %}<span class="pill off">Off</span>{% endif %}</td><td>{{ nfiles }}</td><td>{{ (last or '')[:16]|replace('T',' ') }}</td></tr>{% endfor %}
</tbody></table></div>{% else %}<div class="card empty">No clients yet. Add your first client to give them a portal login and share documents.</div>{% endif %}
</section>""" + ADMIN_FOOT

TABS_SRC = """{% macro ctabs(cid, active) %}<nav class="ctabs" aria-label="Client sections"><a href="/admin/clients/{{ cid }}"{% if active == 'profile' %} class="on" aria-current="page"{% endif %}>Profile and files</a><a href="/admin/clients/{{ cid }}/inbox"{% if active == 'inbox' %} class="on" aria-current="page"{% endif %}>Inbox{% set n = client_unread(cid) %}{% if n %} <span class="n">{{ n }}</span>{% endif %}</a></nav>
<style>.ctabs{display:flex;gap:4px;border-bottom:1px solid var(--line);margin:0 0 18px}.ctabs a{padding:10px 14px;text-decoration:none;color:var(--muted);border-bottom:2px solid transparent;margin-bottom:-1px;font-weight:500;display:inline-flex;align-items:center;gap:8px}
.ctabs a:hover{color:var(--ink)}.ctabs a.on{color:var(--ink);border-bottom-color:var(--cyan)}.ctabs .n{font:600 11px var(--fm);background:var(--cyan);color:#062028;border-radius:999px;padding:2px 7px}</style>{% endmacro %}
"""

ADMIN_CLIENT = U.head("clients", "Client") + TABS_SRC + """
<section><p><a href="/admin/clients">← All clients</a></p><h2>{{ c.company or 'New client' }}</h2>
{% if cid %}{{ ctabs(cid, 'profile') }}{% endif %}
{% if err %}<p class="err" role="alert">{{ err }}</p>{% elif note %}<p class="ok" role="status">{{ note }}</p>{% endif %}
{% if request.args.get('stripe_error') %}<p class="err" role="alert">Stripe: {{ request.args.get('stripe_error') }}</p>{% endif %}
{% if new_pw %}<div class="card" style="margin-bottom:16px"><p class="mono">Client login</p><p>Login page: <a href="{{ login_url }}">{{ login_url }}</a><br>Email: <b>{{ c.contact_email }}</b></p><p class="secret">{{ new_pw }}</p></div>{% endif %}
<div class="split">
<form class="card" method="post" autocomplete="off">
 <p class="mono">Company</p>
 <div class="grid">
  <div class="full"><label class="f" for="company">Company name</label><input type="text" id="company" name="company" value="{{ c.company or '' }}" required></div>
  <div><label class="f" for="website">Website</label><input type="text" id="website" name="website" value="{{ c.website or '' }}" placeholder="company.com"></div>
  <div><label class="f" for="industry">Industry</label><input type="text" id="industry" name="industry" value="{{ c.industry or '' }}"></div>
  <div><label class="f" for="country">Country</label><input type="text" id="country" name="country" value="{{ c.country or '' }}"></div>
  <div><label class="f" for="status">Status</label><select id="status" name="status">{% for s in statuses %}<option{% if c.status == s %} selected{% endif %}>{{ s }}</option>{% endfor %}</select></div>
  <div><label class="f" for="engagement">Engagement type</label><select id="engagement" name="engagement"><option value="">Not set</option>{% for t in types %}<option{% if c.engagement == t %} selected{% endif %}>{{ t }}</option>{% endfor %}</select></div>
  <div><label class="f" for="consultant_id">Consultant</label><select id="consultant_id" name="consultant_id"><option value="">Default{% if cons %} ({{ cons[0].name }}){% endif %}</option>{% for x in cons %}<option value="{{ x.id }}"{% if c.consultant_id == x.id %} selected{% endif %}>{{ x.name }}{% if x.title %} · {{ x.title }}{% endif %}</option>{% endfor %}</select></div>
  <div><label class="f" for="phase">Project phase (shown in the portal)</label><select id="phase" name="phase"><option value="">Not set (shows Introduction)</option>{% for ph in phases %}<option{% if c.phase == ph %} selected{% endif %}>{{ ph }}</option>{% endfor %}</select></div>
  <div class="full"><label class="f" for="address">Address</label><textarea id="address" name="address" style="min-height:70px">{{ c.address or '' }}</textarea></div>
 </div>
 <p class="mono" style="margin-top:22px">Main contact</p>
 <div class="grid">
  <div><label class="f" for="contact_name">Full name</label><input type="text" id="contact_name" name="contact_name" value="{{ c.contact_name or '' }}"></div>
  <div><label class="f" for="contact_title">Job title</label><input type="text" id="contact_title" name="contact_title" value="{{ c.contact_title or '' }}"></div>
  <div><label class="f" for="contact_email">Email (portal login)</label><input type="email" id="contact_email" name="contact_email" value="{{ c.contact_email or '' }}"></div>
  <div><label class="f" for="contact_phone">Phone</label><input type="tel" id="contact_phone" name="contact_phone" value="{{ c.contact_phone or '' }}"></div>
  <div class="full"><label class="f" for="contact_linkedin">LinkedIn</label><input type="url" id="contact_linkedin" name="contact_linkedin" value="{{ c.contact_linkedin or '' }}" placeholder="https://www.linkedin.com/in/..."></div>
  <div class="full"><label class="f" for="notes">Notes (private, not shown to the client)</label><textarea id="notes" name="notes">{{ c.notes or '' }}</textarea></div>
 </div>
 <p class="mono" style="margin-top:22px">Client portal</p>
 <div class="grid">
  <div class="full"><label class="f" for="consultant_note">Note from your consultant (shown at the top of the client's overview)</label><textarea id="consultant_note" name="consultant_note" style="min-height:80px" placeholder="e.g. Next workshop is Tuesday at 10am. Please share last quarter's order export in Your Drive.">{{ c.consultant_note or '' }}</textarea></div>
  <div class="full"><label class="check"><input type="checkbox" id="portal_enabled" name="portal_enabled" value="1"{% if c.portal_enabled %} checked{% endif %}> Portal access on</label></div>
  <div><label class="f" for="password">{% if c.password_hash %}New password (leave blank to keep){% else %}Password{% endif %}</label><input type="password" id="password" name="password" autocomplete="new-password" minlength="10"></div>
  <div style="align-self:end"><label class="check"><input type="checkbox" id="generate" name="generate" value="1"> Generate a password for me</label></div>
  <div class="full"><label class="f" for="stripe_customer_id">Stripe customer ID</label><input type="text" id="stripe_customer_id" name="stripe_customer_id" value="{{ c.stripe_customer_id or '' }}" placeholder="cus_..."></div>
 </div>
 <div class="row" style="margin-top:22px"><button type="submit">{% if cid %}Save changes{% else %}Add client{% endif %}</button>{% if cid %}<a href="/admin/clients" class="btn ghost">Cancel</a>{% endif %}</div>
</form>
<div style="display:grid;gap:18px">
{% if cid %}
 <form class="card" method="post" action="/admin/clients/{{ cid }}/logo" enctype="multipart/form-data"><p class="mono">Company logo</p>
  <p class="empty" style="margin:4px 0 12px;font-size:14px">Shown in the client's portal. PNG, JPG, WebP or SVG, up to 2 MB. A transparent logo works best.</p>
  {% if request.args.get('logo_error') %}<p class="err" role="alert" style="font-size:14px">Logo not saved: use a PNG, JPG, WebP or SVG under 2 MB.</p>{% elif request.args.get('logo_note') %}<p class="ok" role="status" style="font-size:14px">{{ request.args.get('logo_note') }}</p>{% endif %}
  {% if c.logo %}<div style="background:#F4F7FA;border-radius:10px;padding:14px;display:grid;place-items:center;margin-bottom:12px"><img src="/admin/clients/{{ cid }}/logo?v={{ c.logo }}" alt="{{ c.company }} logo" style="max-height:64px;max-width:100%"></div>{% endif %}
  <div class="row"><input type="file" name="logo" accept="image/png,image/jpeg,image/webp,image/svg+xml" aria-label="Logo file" style="flex:1;min-width:0"><button type="submit">{{ 'Replace' if c.logo else 'Upload' }}</button>{% if c.logo %}<button class="ghost" type="submit" name="remove" value="1" formnovalidate>Remove</button>{% endif %}</div>
 </form>
 <div class="card"><p class="mono">Project documents</p><p class="empty" style="margin:4px 0 12px;font-size:14px">Proposals, agreements and other documents the client sees under Project docs.</p>
  <label class="f" for="cat">Document type</label><select id="cat">{% for d in doc_types %}<option>{{ d }}</option>{% endfor %}</select>
  <div class="drop" data-kind="legal" tabindex="0" role="button" style="margin-top:10px"><b>Drop files here</b> or click to choose<br><small>Up to 25 MB each</small></div>
  <ul class="files">{% for f in files if f.kind == 'legal' %}<li><span class="ic">{{ f.name|ext }}</span><span class="nm"><a href="/admin/clients/{{ cid }}/files/{{ f.id }}" target="_blank">{{ f.name }}</a><small>{{ f.category }} · {{ f.size|fsize }} · {{ f.at[:10] }}</small></span><button class="ghost" data-del="{{ f.id }}" type="button">Delete</button></li>{% else %}<li class="empty" style="display:block">No documents yet.</li>{% endfor %}</ul>
 </div>
 <div class="card"><p class="mono">Client drive</p><p class="empty" style="margin:4px 0 12px;font-size:14px">Files in the client's drive. They can add their own; files you add here appear there too.</p>
  <div class="drop" data-kind="drive" tabindex="0" role="button"><b>Drop files here</b> or click to choose</div>
  <ul class="files">{% for f in files if f.kind == 'drive' %}<li><span class="ic">{{ f.name|ext }}</span><span class="nm"><a href="/admin/clients/{{ cid }}/files/{{ f.id }}" target="_blank">{{ f.name }}</a><small>{{ 'From client' if f.uploaded_by == 'client' else 'From you' }} · {{ f.size|fsize }} · {{ f.at[:10] }}</small></span><button class="ghost" data-del="{{ f.id }}" type="button">Delete</button></li>{% else %}<li class="empty" style="display:block">The drive is empty.</li>{% endfor %}</ul>
 </div>
 <div class="card"><p class="mono">Invoices (Stripe)</p>
  {% if not stripe %}<p class="empty" style="font-size:14px">Stripe isn't connected. Add STRIPE_SECRET_KEY in Render to show invoices here and in the client portal.</p>
  {% elif not c.stripe_customer_id %}<p class="empty" style="font-size:14px">No Stripe customer linked yet.</p><form method="post" action="/admin/clients/{{ cid }}/stripe"><button type="submit">Create Stripe customer</button></form>
  {% elif inv_err %}<p class="err" style="font-size:14px">{{ inv_err }}</p>
  {% elif inv %}<ul class="files">{% for i in inv %}<li><span class="ic">INV</span><span class="nm">{% if i.url %}<a href="{{ i.url }}" target="_blank" rel="noopener">{{ i.number }}</a>{% else %}{{ i.number }}{% endif %}<small>{{ i.created }} · {{ i.amount }}</small></span><span class="pill{% if i.status != 'paid' %} off{% endif %}">{{ i.status }}</span></li>{% endfor %}</ul>
  {% else %}<p class="empty" style="font-size:14px">No invoices for {{ c.stripe_customer_id }} yet. Create them in Stripe and they appear here.</p>{% endif %}
 </div>
 <form class="card" method="post" action="/admin/clients/{{ cid }}/delete"><p class="mono">Delete client</p><p class="empty" style="font-size:14px;margin:4px 0 10px">Removes the client, their portal login and all their files. This can't be undone. Type DELETE to confirm.</p>
  <div class="row"><input type="text" name="confirm" aria-label="Type DELETE to confirm" style="max-width:160px" autocomplete="off"><button class="danger" type="submit">Delete client</button></div></form>
{% else %}<div class="card empty">Save the client first, then you can add documents and files.</div>{% endif %}
</div></div>
<input type="file" id="picker" multiple hidden>
</section>
<script>
(function(){
  var cid={{ cid|tojson }}; if(!cid) return;
  var picker=document.getElementById('picker'), kind='drive';
  function up(files,k){
    if(!files||!files.length) return; var fd=new FormData(); fd.append('kind',k); fd.append('category',document.getElementById('cat').value);
    for(var i=0;i<files.length;i++) fd.append('file',files[i]);
    var z=document.querySelector('.drop[data-kind='+k+']'); z.innerHTML='<b>Uploading…</b>';
    fetch('/admin/clients/'+cid+'/files',{method:'POST',body:fd,credentials:'same-origin'}).then(function(r){return r.json()})
      .then(function(j){ if(j.errors&&j.errors.length) alert(j.errors.join('\\n')); location.reload(); })
      .catch(function(){ z.innerHTML='<b>Upload failed.</b> Try again.'; });
  }
  document.querySelectorAll('.drop').forEach(function(z){
    z.addEventListener('click',function(){kind=z.dataset.kind;picker.click()});
    z.addEventListener('keydown',function(e){if(e.key==='Enter'||e.key===' '){e.preventDefault();kind=z.dataset.kind;picker.click()}});
    z.addEventListener('dragover',function(e){e.preventDefault();z.classList.add('over')});
    z.addEventListener('dragleave',function(){z.classList.remove('over')});
    z.addEventListener('drop',function(e){e.preventDefault();z.classList.remove('over');up(e.dataTransfer.files,z.dataset.kind)});
  });
  picker.addEventListener('change',function(){up(picker.files,kind);picker.value=''});
  document.querySelectorAll('[data-del]').forEach(function(b){b.addEventListener('click',function(){
    if(b.dataset.armed!=='1'){b.dataset.armed='1';b.textContent='Confirm delete';b.classList.add('danger');return}
    fetch('/admin/clients/'+cid+'/files/'+b.dataset.del+'/delete',{method:'POST',credentials:'same-origin'}).then(function(){location.reload()});
  })});
})();
</script>""" + ADMIN_FOOT

ADMIN_INBOX = U.head("clients", "Client inbox") + TABS_SRC + """
<section><p><a href="/admin/clients">← All clients</a></p><h2>{{ c.company }}</h2>
{{ ctabs(cid, 'inbox') }}
{% if sent %}<p class="ok" role="status">Message sent. {{ c.contact_name or 'The client' }} sees it in their portal Inbox.</p>{% elif err %}<p class="err" role="alert">Write a message first.</p>{% endif %}
{% if not c.portal_enabled %}<p class="err" role="status">Portal access is off for this client, so they can't read messages yet. Turn it on under Profile and files.</p>{% endif %}
<div class="mbox">
 <div class="card thread"><p class="mono">Conversation with {{ c.contact_name or c.company }} · {{ msgs|length }} message{{ '' if msgs|length == 1 else 's' }}</p>
  {% if msgs %}<ol class="msgs">{% for m in msgs %}<li class="m {{ m.sender }}{% if m.id in new_ids %} new{% endif %}"{% if loop.last %} id="latest"{% endif %}>
   <div class="mh"><b>{{ m.author or ('Client' if m.sender == 'client' else 'Automagic') }}</b><span>{{ 'Client' if m.sender == 'client' else 'Sent to client' }} · {{ m.at[:16]|replace('T',' ') }} UTC</span>
   {% if m.sender == 'consultant' %}<span class="rd">{{ 'Read ' ~ m.read_client[:16]|replace('T',' ') if m.read_client else 'Not read yet' }}</span>{% elif m.id in new_ids %}<span class="pill">New</span>{% endif %}</div>
   {% if m.subject %}<p class="sj">{{ m.subject }}</p>{% endif %}<div class="bd">{{ m.body }}</div></li>{% endfor %}</ol>
  {% else %}<p class="empty">No messages yet. Messages you send appear in the client's portal Inbox, and their replies show up here.</p>{% endif %}
 </div>
 <form class="card compose" method="post"><p class="mono">New message</p>
  <div><label class="f" for="author">From</label><input type="text" id="author" name="author" value="{{ con.name }}"></div>
  <div><label class="f" for="subject">Subject (optional)</label><input type="text" id="subject" name="subject" maxlength="200"></div>
  <div><label class="f" for="body">Message</label><textarea id="body" name="body" required style="min-height:180px" placeholder="Hi {{ (c.contact_name or '').split(' ')[0] or 'there' }}, ..."></textarea></div>
  <div class="row"><button type="submit">Send to client</button></div>
  <p class="empty" style="font-size:13px;margin:0">Delivered to the client's portal Inbox. Every message is kept here as a record.</p>
 </form>
</div>
<style>.mbox{display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:18px;align-items:start}@media (max-width:1000px){.mbox{grid-template-columns:minmax(0,1fr)}}
.compose{display:grid;gap:12px;position:sticky;top:20px}.msgs{list-style:none;margin:12px 0 0;padding:0;display:grid;gap:12px}
.m{border:1px solid var(--line);border-radius:12px;padding:14px 16px;max-width:85%;min-width:0}.m.consultant{margin-left:auto;background:rgba(127,211,228,.06);border-color:rgba(127,211,228,.3)}
.m.new{border-color:var(--cyan)}.mh{display:flex;flex-wrap:wrap;gap:4px 10px;align-items:baseline;font-size:13px;color:var(--faint)}.mh b{color:var(--ink);font-size:14.5px}.mh .rd{margin-left:auto}
.sj{margin:8px 0 0;font-weight:600}.bd{margin-top:6px;white-space:pre-wrap;word-wrap:break-word;line-height:1.55}
@media (max-width:640px){.m{max-width:100%}}</style>
</section>""" + ADMIN_FOOT

ADMIN_PRICING = U.head("pricing", "Pricing page") + """
<section><div class="row" style="justify-content:space-between"><h2 style="margin:0">Pricing page</h2></div>
<p class="empty" style="margin:10px 0 16px">Edits the Engagement &amp; pricing page every client sees in their portal. Leave a title blank to hide that card or step. To see the result, sign in to the portal as a client (for example the demo client).{% if not custom %} You're seeing the default content.{% endif %}</p>
{% if note %}<p class="ok" role="status">{{ note }}</p>{% endif %}
<form method="post" class="pcms">
 <div class="card"><h3>Page header</h3><div class="grid">
  <div><label class="f" for="label">Small label</label><input type="text" id="label" name="label" value="{{ pr.label }}"></div>
  <div><label class="f" for="heading">Heading</label><input type="text" id="heading" name="heading" value="{{ pr.heading }}"></div>
  <div class="full"><label class="f" for="intro">Intro</label><textarea id="intro" name="intro" rows="3">{{ pr.intro }}</textarea></div>
 </div></div>
 <div class="card"><h3>Promises</h3><p class="empty">Short ticked statements shown above the cards.</p><div class="grid">
  {% for x in pr.principles %}<div><label class="f" for="pr{{ loop.index0 }}_title">Promise {{ loop.index }}: bold text</label><input type="text" id="pr{{ loop.index0 }}_title" name="pr{{ loop.index0 }}_title" value="{{ x.title }}"></div>
  <div><label class="f" for="pr{{ loop.index0 }}_text">Promise {{ loop.index }}: detail</label><input type="text" id="pr{{ loop.index0 }}_text" name="pr{{ loop.index0 }}_text" value="{{ x.text }}"></div>{% endfor %}
 </div></div>
 <div class="card"><h3>Pricing cards</h3><p class="empty">Shown in this order. The price line shows only when Price is filled in, for example "Starting from" + "$4,500" + "a month".</p>
  {% for x in pr['items'] %}{% set i = loop.index0 %}<fieldset class="pcard"><legend>Card {{ loop.index }}{% if not x.title %} (empty, hidden){% endif %}</legend><div class="grid">
   <div><label class="f" for="it{{ i }}_title">Title</label><input type="text" id="it{{ i }}_title" name="it{{ i }}_title" value="{{ x.title }}"></div>
   <div><label class="f" for="it{{ i }}_badge">Badge</label><input type="text" id="it{{ i }}_badge" name="it{{ i }}_badge" value="{{ x.badge }}" placeholder="Monthly"></div>
   <div class="g3 full">
    <div><label class="f" for="it{{ i }}_prefix">Before price</label><input type="text" id="it{{ i }}_prefix" name="it{{ i }}_prefix" value="{{ x.prefix }}" placeholder="Starting from"></div>
    <div><label class="f" for="it{{ i }}_price">Price</label><input type="text" id="it{{ i }}_price" name="it{{ i }}_price" value="{{ x.price }}" placeholder="$4,500"></div>
    <div><label class="f" for="it{{ i }}_unit">After price</label><input type="text" id="it{{ i }}_unit" name="it{{ i }}_unit" value="{{ x.unit }}" placeholder="a month"></div>
   </div>
   <div class="full"><label class="f" for="it{{ i }}_text">Description</label><textarea id="it{{ i }}_text" name="it{{ i }}_text" rows="2">{{ x.text }}</textarea></div>
   <div><label class="f" for="it{{ i }}_link_label">Link text (optional)</label><input type="text" id="it{{ i }}_link_label" name="it{{ i }}_link_label" value="{{ x.link_label }}"></div>
   <div><label class="f" for="it{{ i }}_link_url">Link URL</label><input type="text" id="it{{ i }}_link_url" name="it{{ i }}_link_url" value="{{ x.link_url }}" placeholder="/hunter/ or https://..."></div>
   <label class="chk full"><input type="checkbox" name="it{{ i }}_outline" value="1"{% if x.outline %} checked{% endif %}> Dashed outline (for costs the client pays directly)</label>
  </div></fieldset>{% endfor %}
 </div>
 <div class="card"><h3>How it fits together</h3><div class="grid">
  <div class="full"><label class="f" for="flow_title">Section label</label><input type="text" id="flow_title" name="flow_title" value="{{ pr.flow_title }}"></div>
  {% for x in pr.flow %}<div><label class="f" for="fl{{ loop.index0 }}_title">Step {{ loop.index }}: title</label><input type="text" id="fl{{ loop.index0 }}_title" name="fl{{ loop.index0 }}_title" value="{{ x.title }}"></div>
  <div><label class="f" for="fl{{ loop.index0 }}_text">Step {{ loop.index }}: detail</label><input type="text" id="fl{{ loop.index0 }}_text" name="fl{{ loop.index0 }}_text" value="{{ x.text }}"></div>{% endfor %}
  <div class="full"><label class="f" for="note">Closing note</label><input type="text" id="note" name="note" value="{{ pr.note }}"><small class="empty">A link to Project docs (when the client has any) and "Ask [consultant]" are added after it automatically.</small></div>
 </div></div>
 <div class="row savebar"><button type="submit">Save pricing page</button></div>
</form>
{% if custom %}<form method="post" class="card" style="margin-top:18px"><input type="hidden" name="reset" value="1"><p class="mono">Reset</p><p class="empty" style="font-size:14px;margin:4px 0 10px">Go back to the default content. Your edits will be lost.</p><button class="danger" type="submit" onclick="return confirm('Reset the pricing page to the default content?')">Reset to default</button></form>{% endif %}
<style>.pcms{display:grid;gap:18px;max-width:900px}.pcms h3{margin:0 0 6px}.pcms p.empty{margin:0 0 14px;font-size:14px}
.pcard{border:1px solid var(--line);border-radius:12px;padding:14px 16px 16px;margin:0 0 14px;min-width:0}.pcard legend{padding:0 6px;font-weight:600}
.g3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}@media (max-width:640px){.g3{grid-template-columns:minmax(0,1fr)}}
.chk{display:flex;gap:8px;align-items:center;font-size:14px}.chk input{width:auto}
.savebar{position:sticky;bottom:0;padding:12px 0;background:var(--bg)}</style>
</section>""" + ADMIN_FOOT

ADMIN_CONSULTANTS = U.head("consultants", "Consultants") + """
<section><div class="row" style="justify-content:space-between"><h2 style="margin:0">Consultants</h2><a class="btn" href="/admin/consultants/new">Add consultant</a></div>
<p class="empty" style="margin:10px 0 16px">Each client sees their assigned consultant under Your Consultant. Clients with no consultant assigned see the default, the first one listed.</p>
{% if note %}<p class="ok" role="status">{{ note }}</p>{% endif %}
{% if cons %}<div class="tw"><table><thead><tr><th>Name</th><th>Title</th><th>Email</th><th>Clients</th></tr></thead><tbody>
{% for x in cons %}<tr class="rowlink" onclick="location='/admin/consultants/{{ x.id }}'"><td><a href="/admin/consultants/{{ x.id }}">{{ x.name }}</a>{% if loop.first %} <span class="pill">Default</span>{% endif %}</td><td>{{ x.title }}</td><td>{{ x.email }}</td><td>{{ counts.get(x.id, 0) }}</td></tr>{% endfor %}
</tbody></table></div>{% else %}<div class="card empty">No consultants yet. Clients see the built-in Automagic profile until you add one.</div>{% endif %}
</section>""" + ADMIN_FOOT

ADMIN_CONSULTANT = U.head("consultants", "Consultant") + """
<section><p><a href="/admin/consultants">← All consultants</a></p><h2>{{ c.name or 'New consultant' }}{% if is_default %} <span class="pill">Default</span>{% endif %}</h2>
{% if pid %}<p class="empty" style="margin:0 0 16px">Assigned to {{ nclients }} client{{ '' if nclients == 1 else 's' }}{% if is_default %}, and shown to every client with no consultant assigned{% endif %}.</p>{% endif %}
{% if err %}<p class="err" role="alert">{{ err }}</p>{% elif note %}<p class="ok" role="status">{{ note }}</p>{% endif %}
<form class="card" method="post" enctype="multipart/form-data" style="max-width:760px"><div class="grid">
 <div class="full row" style="gap:16px">{% if c.photo %}<img src="/consultant-photo/{{ pid }}" alt="" width="72" height="72" style="border-radius:50%;object-fit:cover">{% endif %}<div style="flex:1;min-width:200px"><label class="f" for="photo">Photo (PNG, JPG or WebP)</label><input type="file" id="photo" name="photo" accept="image/png,image/jpeg,image/webp"></div></div>
 <div><label class="f" for="name">Name</label><input type="text" id="name" name="name" value="{{ c.name }}" required></div>
 <div><label class="f" for="title">Title</label><input type="text" id="title" name="title" value="{{ c.title }}"></div>
 <div><label class="f" for="email">Email</label><input type="email" id="email" name="email" value="{{ c.email }}"></div>
 <div><label class="f" for="phone">Phone</label><input type="tel" id="phone" name="phone" value="{{ c.phone }}"></div>
 <div class="full"><label class="f" for="linkedin">LinkedIn</label><input type="url" id="linkedin" name="linkedin" value="{{ c.linkedin }}" placeholder="https://www.linkedin.com/in/..."></div>
 <div class="full"><label class="f" for="cal_url">Booking link</label><input type="url" id="cal_url" name="cal_url" value="{{ c.cal_url }}" placeholder="https://cal.com/..."></div>
 <div class="full"><label class="f" for="bio">Bio</label><textarea id="bio" name="bio">{{ c.bio }}</textarea></div>
</div><div class="row" style="margin-top:18px"><button type="submit">{% if pid %}Save profile{% else %}Add consultant{% endif %}</button></div></form>
{% if pid %}<form class="card" method="post" action="/admin/consultants/{{ pid }}/delete" style="max-width:760px;margin-top:18px"><p class="mono">Delete consultant</p><p class="empty" style="font-size:14px;margin:4px 0 10px">Their clients move to the default consultant. This can't be undone. Type DELETE to confirm.</p>
 <div class="row"><input type="text" name="confirm" aria-label="Type DELETE to confirm" style="max-width:160px" autocomplete="off"><button class="danger" type="submit">Delete consultant</button></div></form>{% endif %}
</section>""" + ADMIN_FOOT

ICON = {
 "home": '<path d="M3 10.5 12 3l9 7.5V21h-6v-6H9v6H3z"/>',
 "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7"/>',
 "doc": '<path d="M6 2h9l5 5v15H6z"/><path d="M14 2v6h6M9 13h8M9 17h8"/>',
 "card": '<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M2 10h20M6 15h4"/>',
 "drive": '<path d="M3 7h6l2 2h10v10H3z"/>',
 "gear": '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9 7 7M17 17l2.1 2.1M4.9 19.1 7 17M17 7l2.1-2.1"/>',
 "mail": '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m2 6 10 7 10-7"/>',
 "phone": '<path d="M5 3h4l2 5-2.5 1.5a11 11 0 0 0 6 6L16 13l5 2v4a2 2 0 0 1-2 2A17 17 0 0 1 3 5a2 2 0 0 1 2-2z"/>',
 "in": '<rect x="2" y="2" width="20" height="20" rx="3"/><path d="M7 10v7M7 7v.01M11 17v-4a2 2 0 0 1 4 0v4M11 10v7"/>',
 "book": '<path d="M4 4.5A1.5 1.5 0 0 1 5.5 3H20v15H5.5A1.5 1.5 0 0 0 4 19.5z"/><path d="M4 19.5A1.5 1.5 0 0 0 5.5 21H20v-3M8 7h8M8 11h6"/>',
 "ext": '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
 "cal": '<rect x="3" y="4" width="18" height="17" rx="2"/><path d="M3 9h18M8 2v4M16 2v4"/>',
 "tag": '<path d="M3 3h8l10 10-8 8L3 11z"/><circle cx="7.5" cy="7.5" r="1.5"/>',
 "calc": '<rect x="4" y="2" width="16" height="20" rx="2"/><path d="M8 6h8M8 11h.01M12 11h.01M16 11h.01M8 15h.01M12 15h.01M16 15h.01M8 19h.01M12 19h4"/>',
 "check": '<path d="m5 12 5 5 9-10"/>',
}


def icon(name, size=18):
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICON[name]}</svg>')


S.app.jinja_env.globals["icon"] = icon


def client_unread(cid):
    """Messages from the client the team hasn't opened yet."""
    return S.q("SELECT COUNT(*) FROM messages WHERE client_id = ? AND sender = 'client' AND read_admin IS NULL", (cid,), one=True)[0]


S.app.jinja_env.globals["client_unread"] = client_unread

PORTAL = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Client Portal · Automagic</title><meta name="robots" content="noindex">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root{--bg:#0A1018;--bg-2:#0F1722;--surface:#131D2A;--surface-2:#182434;--ink:#E8EEF5;--muted:#9BA9BA;--faint:#6E7D90;--line:#223043;--line-2:#2D3D53;
--cyan:#7FD3E4;--cyan-hi:#B6E6F0;--cyan-deep:#1B3B47;--on-cyan:#062028;--ok:#7FD3A6;--warn:#F0C987;--err:#F2B8B5;
--fd:"Space Grotesk","Segoe UI",system-ui,sans-serif;--fb:"Inter","Segoe UI",system-ui,sans-serif;--fm:"IBM Plex Mono",ui-monospace,Menlo,monospace;color-scheme:dark}
*{box-sizing:border-box}html{background:var(--bg)}body{margin:0;background:var(--bg);color:var(--ink);font:15.5px/1.6 var(--fb);-webkit-font-smoothing:antialiased}
a{color:var(--cyan)}:focus-visible{outline:2px solid var(--cyan);outline-offset:2px;border-radius:4px}
h1,h2,h3{font-family:var(--fd);margin:0;letter-spacing:-.02em;text-wrap:balance}
.top{position:sticky;top:0;z-index:20;background:rgba(10,16,24,.88);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border-bottom:1px solid var(--line)}
.top .in{max-width:1240px;margin:0 auto;height:64px;display:flex;align-items:center;gap:16px;padding-inline:max(16px,env(safe-area-inset-left,0px)) max(16px,env(safe-area-inset-right,0px))}
.top img{height:22px;display:block}.top .who{margin-left:auto;font-size:14px;color:var(--muted);text-align:right;line-height:1.3;min-width:0}
.top .who b{display:block;color:var(--ink);font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
@media (max-width:560px){.top .who{font-size:0}.top .who b{font-size:14px}}
.btn{display:inline-flex;align-items:center;gap:8px;font:600 14.5px var(--fd);border-radius:7px;padding:10px 16px;border:1px solid transparent;cursor:pointer;text-decoration:none;white-space:nowrap}
.btn-p{background:var(--cyan);color:var(--on-cyan)}.btn-p:hover{background:var(--cyan-hi)}
.btn-g{background:transparent;color:var(--ink);border-color:var(--line-2)}.btn-g:hover{border-color:var(--cyan)}
.shell{max-width:1240px;margin:0 auto;display:grid;grid-template-columns:230px minmax(0,1fr);gap:36px;padding:32px max(16px,env(safe-area-inset-right,0px)) 64px max(16px,env(safe-area-inset-left,0px))}
.side{position:sticky;top:96px;align-self:start;display:grid;gap:4px}
.side a{display:flex;align-items:center;gap:12px;padding:10px 12px;border-radius:8px;color:var(--muted);text-decoration:none;font-weight:500}
.side a:hover{color:var(--ink);background:var(--surface)}.side a.on{color:var(--ink);background:var(--surface-2);box-shadow:inset 3px 0 0 var(--cyan)}
.side a svg{flex:none}
@media (max-width:900px){.shell{grid-template-columns:minmax(0,1fr);gap:20px;padding-top:16px}
 .side{position:static;display:flex;overflow-x:auto;gap:6px;scrollbar-width:none;margin-inline:-16px;padding-inline:16px}.side::-webkit-scrollbar{display:none}
 .side a{flex:none;border:1px solid var(--line);padding:8px 12px;font-size:14px}.side a.on{box-shadow:none;border-color:var(--cyan)}}
.view[hidden]{display:none}
.side a.ext{position:relative}.side a.ext svg:last-child{margin-left:auto;opacity:.6}
.side a.sep{margin-top:10px;border-top:1px solid var(--line);border-radius:0 0 8px 8px;padding-top:14px}
@media (max-width:900px){.side a.sep{margin-top:0;border-radius:8px;padding-top:8px;border:1px solid var(--line)}.side a.sep.on{border-color:var(--cyan)}}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.foot{border-top:1px solid var(--line);background:var(--bg-2)}
.foot .in{max-width:1240px;margin:0 auto;display:flex;align-items:center;gap:12px 28px;flex-wrap:wrap;padding:20px max(16px,env(safe-area-inset-right,0px)) calc(20px + env(safe-area-inset-bottom,0px)) max(16px,env(safe-area-inset-left,0px));font-size:13.5px;color:var(--faint)}
.foot nav{display:flex;gap:6px 20px;flex-wrap:wrap}
.foot a{color:var(--muted);text-decoration:none}.foot a:hover{color:var(--ink)}
.foot .help{margin-left:auto}.foot .help a{color:var(--cyan)}
@media (max-width:700px){.foot .help{margin-left:0;width:100%}}
.label{display:inline-flex;align-items:center;gap:9px;font:500 12px var(--fm);letter-spacing:.14em;text-transform:uppercase;color:var(--cyan)}
.label::before{content:"";width:8px;height:8px;background:var(--cyan)}
.vh{display:grid;gap:10px;margin-bottom:24px}.vh h2{font-size:clamp(26px,3vw,34px);font-weight:600}.vh p{color:var(--muted);max-width:62ch;margin:0}
.hero{position:relative;overflow:hidden;border:1px solid var(--line-2);border-radius:14px;padding:clamp(24px,4vw,44px);background:radial-gradient(ellipse 60% 90% at 90% 0%,rgba(127,211,228,.16),transparent 65%),linear-gradient(160deg,#10202D,var(--bg-2))}
.hero h1{font-size:clamp(32px,4.4vw,52px);font-weight:600;line-height:1.05;margin:14px 0 10px}
.hero h1 span{color:var(--cyan)}.hero p.co{color:var(--ink);font:600 clamp(18px,2vw,22px) var(--fd);letter-spacing:-.01em;margin:-4px 0 14px}.hero p{color:var(--muted);max-width:62ch;margin:0;font-size:17px}
/* overview */
.hero-top{display:flex;align-items:center;justify-content:space-between;gap:20px}
.clogo{flex:none;width:clamp(96px,14vw,168px);height:clamp(64px,8vw,96px);border-radius:12px;background:#F4F7FA;display:grid;place-items:center;padding:12px 16px;box-shadow:0 8px 24px rgba(0,0,0,.25)}
.clogo img{max-width:100%;max-height:100%;object-fit:contain;display:block}
@media (max-width:560px){.hero-top{flex-direction:column-reverse;align-items:flex-start}.clogo{width:120px;height:64px}}
.top .who{display:flex;align-items:center;gap:12px}.top .who>span{min-width:0}
.hlogo{flex:none;width:36px;height:36px;border-radius:8px;background:#F4F7FA;display:grid;place-items:center;padding:4px}
.hlogo img{max-width:100%;max-height:100%;object-fit:contain;display:block}
.hero.slim{padding:clamp(22px,3vw,32px) clamp(20px,3vw,34px)}
.hero.slim h1{font-size:clamp(30px,3.6vw,44px);margin:12px 0 6px}
.hero.slim p.co{margin:0}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px}
.chip{font:500 12px var(--fm);letter-spacing:.05em;padding:5px 10px;border-radius:999px;border:1px solid var(--line-2);color:var(--muted);background:rgba(10,16,24,.35)}
.chip b{color:var(--ink);font-weight:500}.chip.ok{color:var(--ok);border-color:rgba(127,211,166,.4)}
.phases{list-style:none;margin:24px 0 0;padding:0;display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:8px;counter-reset:ph}
.phases li{font:500 12px var(--fm);letter-spacing:.04em;color:var(--faint);min-width:0}.phases li a{display:grid;gap:8px;color:inherit;text-decoration:none}.phases li a:hover{color:var(--ink)}
.phases li a::before{content:"";height:6px;border-radius:3px;background:var(--line-2)}
.phases li.done{color:var(--muted)}.phases li.done a::before{background:rgba(127,211,228,.55)}
.phases li.now{color:var(--cyan)}.phases li.now a::before{background:var(--cyan);box-shadow:0 0 12px rgba(127,211,228,.55)}
.phases li span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hero p.hww{margin:14px 0 0;font-size:14px}.hww a{text-decoration:none}
@media (max-width:560px){.phases{grid-template-columns:repeat(6,minmax(0,1fr));gap:4px}.phases li span{display:none}.phases li.now span{display:block;overflow:visible;grid-column:1}}
.ov{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:20px;margin-top:20px;align-items:start}
.ov-main{display:grid;gap:20px;min-width:0}
@media (max-width:1060px){.ov{grid-template-columns:minmax(0,1fr)}.ov-side{order:-1}}
.intro{border-color:rgba(127,211,228,.35);background:linear-gradient(160deg,rgba(127,211,228,.10),var(--surface) 65%)}
.intro h3{font-size:clamp(20px,2.2vw,24px);margin:10px 0 8px}.intro>p{margin:0;color:var(--muted);line-height:1.6;max-width:62ch}
.isteps{margin:16px 0 0;padding-left:20px;display:grid;gap:8px;color:var(--muted);line-height:1.55}.isteps b{color:var(--ink);font-weight:600}
.iact{display:flex;flex-wrap:wrap;gap:10px;margin-top:18px}
.side .badge{margin-left:auto;font:600 11px var(--fm);background:var(--cyan);color:var(--on-cyan);border-radius:999px;padding:2px 8px}
.ov-side{display:grid;gap:16px;min-width:0}
.ibx{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:18px;align-items:start}@media (max-width:1000px){.ibx{grid-template-columns:minmax(0,1fr)}}
.msgs{list-style:none;margin:0;padding:0;display:grid;gap:12px}.m{border:1px solid var(--line);border-radius:12px;padding:14px 16px;min-width:0;max-width:88%}
.m.me{margin-left:auto;background:rgba(127,211,228,.06);border-color:rgba(127,211,228,.3)}.m.new{border-color:var(--cyan);box-shadow:0 0 0 1px rgba(127,211,228,.25)}
.mh{display:flex;flex-wrap:wrap;gap:4px 10px;align-items:center;font-size:13px;color:var(--faint)}.mh b{color:var(--ink);font-size:15px}
.m .sj{margin:8px 0 0;font-weight:600}.m .bd{margin-top:6px;white-space:pre-wrap;word-wrap:break-word;line-height:1.6;color:var(--ink)}
@media (max-width:640px){.m{max-width:100%}}
.f textarea{width:100%;font:15px var(--fb);color:var(--ink);background:var(--bg);border:1.5px solid var(--line-2);border-radius:9px;padding:11px 12px;resize:vertical}.f textarea:focus{outline:none;border-color:var(--cyan)}
.cnote{position:relative;background:#F7FAFC;color:#14202E;border:0;border-radius:14px;padding:22px 22px 18px;box-shadow:0 12px 32px rgba(0,0,0,.35);transform:rotate(-.4deg)}
.cnote::before{content:"";position:absolute;top:-9px;left:50%;width:72px;height:18px;margin-left:-36px;background:rgba(127,211,228,.55);border-radius:3px;transform:rotate(2deg)}
.cnote .nh{display:flex;align-items:center;gap:10px}
.cnote .av{flex:none;width:34px;height:34px;border-radius:50%;object-fit:cover;background:#DCEFF4;color:#1B3B47;display:grid;place-items:center;font:600 15px var(--fd)}
.cnote .nh b{display:block;font:600 15px var(--fd);color:#0A1018}.cnote .nh div span{display:block;font:500 10.5px var(--fm);letter-spacing:.1em;text-transform:uppercase;color:#5B6B7E}
.cnote p{margin:14px 0 0;white-space:pre-line;font-size:15px;line-height:1.65;color:#22303F}
.cnote a{display:inline-block;margin-top:14px;font-size:13.5px;font-weight:600;color:#1C6E80;text-decoration:none}.cnote a:hover{text-decoration:underline}
.card.panel{padding:0;overflow:hidden}
.panel .hd{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:16px 20px;border-bottom:1px solid var(--line)}
.panel .hd h3{font-size:17px;font-weight:600;display:flex;align-items:center;gap:10px}.panel .hd h3 svg{color:var(--cyan)}
.panel .hd a{font-size:13.5px;text-decoration:none;white-space:nowrap}
.rows{list-style:none;margin:0;padding:0}
.rows li{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:14px;align-items:center;padding:12px 20px;border-top:1px solid var(--line)}
.rows li:first-child{border-top:0}
.rows .mini{width:34px;height:40px;border-radius:3px 9px 3px 3px;background:var(--surface-2);border:1px solid var(--line-2);display:grid;place-items:end center;padding-bottom:5px;font:600 8.5px var(--fm);color:var(--cyan)}
.rows .tx{min-width:0}.rows .tx b{display:block;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rows .tx span{display:block;color:var(--faint);font-size:13px}
.rows .amt{font:600 16px var(--fd);font-variant-numeric:tabular-nums}
.panel .none{padding:18px 20px;color:var(--muted);font-size:14.5px;margin:0}
.panel .drop{margin:16px 20px 4px;padding:18px;font-size:14px}
.btn-s{padding:7px 12px;font-size:13.5px}
.cc{display:grid;gap:16px}
.cc-h{display:grid;grid-template-columns:64px minmax(0,1fr);gap:14px;align-items:center}
.cc-h .ph{width:64px;height:64px;border-radius:12px;font-size:26px}
.cc-h b{display:block;font:600 18px var(--fd);letter-spacing:-.01em}.cc-h span{display:block;color:var(--cyan);font:500 11.5px var(--fm);letter-spacing:.05em;text-transform:uppercase;margin-top:3px}
.cc-l{display:flex;gap:8px}
.cc-l a{width:42px;height:42px;display:grid;place-items:center;border:1px solid var(--line-2);border-radius:9px;color:var(--cyan)}
.cc-l a:hover{border-color:var(--cyan)}
.cc .btn{justify-content:center}
.cc .more{font-size:13.5px;text-decoration:none}
.tiles{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:18px}
@media (max-width:700px){.tiles{grid-template-columns:minmax(0,1fr)}}
.tile{display:grid;gap:6px;padding:18px;border:1px solid var(--line);border-radius:12px;background:var(--surface);text-decoration:none;color:var(--ink);transition:border-color .15s}
.tile:hover{border-color:rgba(127,211,228,.5)}.tile svg{color:var(--cyan)}.tile b{font:600 17px var(--fd)}.tile span{color:var(--muted);font-size:13.5px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:clamp(18px,3vw,28px)}
.con{display:grid;grid-template-columns:150px minmax(0,1fr);gap:28px;align-items:start}
@media (max-width:640px){.con{grid-template-columns:minmax(0,1fr)}}
.ph{width:150px;height:150px;border-radius:14px;object-fit:cover;background:var(--surface-2);display:grid;place-items:center;font:600 46px var(--fd);color:var(--cyan);border:1px solid var(--line-2)}
.con h3{font-size:26px;font-weight:600}.con .t{color:var(--cyan);font:500 13px var(--fm);letter-spacing:.06em;text-transform:uppercase;margin:4px 0 14px}
.con .bio{color:var(--muted);white-space:pre-line;max-width:64ch}
.links{display:flex;flex-wrap:wrap;gap:10px;margin-top:20px}
.ib{display:inline-flex;align-items:center;gap:9px;padding:9px 13px;border:1px solid var(--line-2);border-radius:8px;color:var(--ink);text-decoration:none;font-size:14.5px}
.ib:hover{border-color:var(--cyan)}.ib svg{color:var(--cyan)}
.docs{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:14px}
.doc{display:grid;justify-items:center;gap:10px;text-align:center;padding:20px 12px 16px;border:1px solid var(--line);border-radius:12px;background:var(--surface);text-decoration:none;color:var(--ink);min-width:0;position:relative;transition:border-color .15s,transform .15s}
.doc:hover{border-color:rgba(127,211,228,.5);transform:translateY(-2px)}
.fi{position:relative;width:58px;height:72px;background:var(--surface-2);border:1px solid var(--line-2);border-radius:4px 16px 4px 4px;display:grid;place-items:end center;padding-bottom:9px}
.fi::after{content:"";position:absolute;right:-1px;top:-1px;width:16px;height:16px;background:linear-gradient(225deg,var(--bg) 50%,var(--line-2) 50%);border-radius:0 0 0 4px}
.fi i{font:600 10.5px var(--fm);font-style:normal;padding:2px 6px;border-radius:3px;background:var(--cyan);color:var(--on-cyan)}
.fi.pdf i{background:#F2A0A0;color:#2a0b0b}.fi.doc i,.fi.docx i{background:#9DBBF2;color:#0b1a33}.fi.xls i,.fi.xlsx i,.fi.csv i{background:#8FD8A9;color:#0b2a16}.fi.ppt i,.fi.pptx i,.fi.key i{background:#F0C987;color:#2e1f05}
.doc .n{font-size:14px;font-weight:500;line-height:1.35;word-break:break-word;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.doc small{color:var(--faint);font:500 11px var(--fm);letter-spacing:.06em;text-transform:uppercase}
.doc .x{position:absolute;top:6px;right:6px;width:28px;height:28px;border-radius:6px;border:1px solid var(--line-2);background:var(--bg-2);color:var(--muted);cursor:pointer;font-size:15px;line-height:1;opacity:0;transition:opacity .15s}
.doc:hover .x,.doc .x:focus,.doc .x.armed{opacity:1}.doc .x.armed{color:var(--err);border-color:var(--err);width:auto;padding:0 8px;font:500 12px var(--fb)}
@media (hover:none){.doc .x{opacity:1}}
.group{margin-bottom:26px}.group h3{font:500 12px var(--fm);letter-spacing:.12em;text-transform:uppercase;color:var(--faint);margin:0 0 12px}
.empty{padding:28px;border:1px dashed var(--line-2);border-radius:12px;color:var(--muted);text-align:center}
.drop{border:1.5px dashed var(--line-2);border-radius:14px;padding:34px 20px;text-align:center;color:var(--muted);cursor:pointer;transition:border-color .15s,background .15s;margin-bottom:22px;background:var(--bg-2)}
.drop svg{color:var(--cyan);margin-bottom:8px}.drop b{color:var(--ink);font-weight:600}
.drop.over{border-color:var(--cyan);background:rgba(127,211,228,.07)}
.tw{overflow-x:auto;border:1px solid var(--line);border-radius:12px}
table{width:100%;border-collapse:collapse;font-size:14.5px}th{text-align:left;font:500 11px var(--fm);letter-spacing:.08em;text-transform:uppercase;color:var(--faint);padding:12px 14px;background:var(--bg-2);white-space:nowrap}
td{padding:12px 14px;border-top:1px solid var(--line);white-space:nowrap}td.num{font-variant-numeric:tabular-nums}
.st{font:500 11px var(--fm);letter-spacing:.06em;text-transform:uppercase;padding:3px 8px;border-radius:999px;border:1px solid var(--line-2);color:var(--muted)}
.st.paid{color:var(--ok);border-color:rgba(127,211,166,.4)}.st.open{color:var(--warn);border-color:rgba(240,201,135,.4)}
.grid2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}
@media (max-width:900px){.grid2{grid-template-columns:minmax(0,1fr)}}
.fg{display:grid;gap:14px}.f label{display:block;font:500 11px var(--fm);letter-spacing:.1em;text-transform:uppercase;color:var(--faint);margin-bottom:6px}
.f input{width:100%;font:15px var(--fb);color:var(--ink);background:var(--bg);border:1.5px solid var(--line-2);border-radius:9px;padding:11px 12px}
.f input:focus{outline:none;border-color:var(--cyan)}.f input[readonly]{color:var(--muted);border-style:dashed}
.note{margin:0 0 18px;padding:12px 16px;border:1px solid rgba(127,211,228,.35);border-radius:10px;background:rgba(127,211,228,.07)}
.prin{list-style:none;margin:0 0 22px;padding:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}
.prin li{display:flex;gap:10px;align-items:flex-start;padding:14px 16px;border:1px solid var(--line);border-radius:12px;background:var(--bg-2);font-size:14px;color:var(--muted);line-height:1.5}
.prin li svg{flex:none;color:var(--ok);margin-top:2px}.prin b{color:var(--ink);font-weight:600}
.pm{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
.pc{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:22px;display:grid;align-content:start;gap:10px}
.pc.ext{background:transparent;border-style:dashed}.pc:last-child:nth-child(odd){grid-column:1/-1}
.pt{display:flex;justify-content:space-between;align-items:center;gap:10px}.pn{font:500 12px var(--fm);color:var(--faint);letter-spacing:.1em}
.pu{font:500 11px var(--fm);letter-spacing:.08em;text-transform:uppercase;padding:4px 9px;border-radius:999px;border:1px solid rgba(127,211,228,.4);color:var(--cyan);background:rgba(127,211,228,.07)}
.pc h3{font-size:19px;font-weight:600}.pc p.pp{color:var(--faint);font-size:13.5px}.pp b{font:600 22px var(--fd);color:var(--ink);margin:0 4px}.pp small{font-size:13.5px}.pc p{margin:0;color:var(--muted);line-height:1.55;font-size:14.5px}.pl{font-size:14px;text-decoration:none}
.pflow{margin-top:14px}.pflow ol{list-style:none;margin:16px 0 0;padding:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;counter-reset:f}
.pflow li{counter-increment:f;display:grid;gap:4px;padding:14px;border-radius:10px;background:var(--bg-2);border:1px solid var(--line)}
.pflow li::before{content:counter(f);font:500 12px var(--fm);color:var(--cyan)}.pflow li b{font:600 15px var(--fd)}.pflow li span{color:var(--muted);font-size:13.5px}
.pflow>p{margin:16px 0 0;color:var(--muted);font-size:14.5px}
@media (max-width:900px){.prin,.pm,.pflow ol{grid-template-columns:minmax(0,1fr)}}
.roi{display:grid;grid-template-columns:minmax(0,380px) minmax(0,1fr);gap:18px;align-items:start}.roi .fg{grid-template-columns:minmax(0,1fr);min-width:0}
@media (max-width:1000px){.roi{grid-template-columns:minmax(0,1fr)}}
.rh{font-size:17px;font-weight:600;margin-top:6px}.f output{color:var(--cyan)}
.f input[type=range]{padding:0;border:0;background:transparent;accent-color:var(--cyan);height:28px}
.kpis{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.kpi{display:grid;gap:6px;padding:18px;border:1px solid var(--line);border-radius:12px;background:var(--surface)}
.kpi span{font:500 11px var(--fm);letter-spacing:.08em;text-transform:uppercase;color:var(--faint)}.kpi b{font:600 clamp(24px,3vw,32px) var(--fd);font-variant-numeric:tabular-nums}
.kpi.hi{border-color:rgba(127,211,228,.45);background:linear-gradient(160deg,rgba(127,211,228,.1),var(--surface) 70%)}.kpi.hi b{color:var(--cyan)}
td.neg{color:var(--err)}.rnote{margin:14px 0 0;color:var(--faint);font-size:13.5px;line-height:1.55}
.kv{display:grid;grid-template-columns:140px minmax(0,1fr);gap:8px 14px;font-size:14.5px;margin:0}.kv dt{color:var(--faint)}.kv dd{margin:0}
</style></head><body>
<header class="top"><div class="in"><a href="/" aria-label="Automagic home"><img src="/brand/wordmark-light.svg" alt="Automagic"></a>
<div class="who">{% if c.logo %}<span class="hlogo"><img src="/portal/logo?v={{ c.logo }}" alt=""></span>{% endif %}<span><b>{{ c.company }}</b>{{ c.contact_email }}</span></div>
<form method="post" action="/portal/logout" style="margin:0"><button class="btn btn-g" type="submit">Sign out</button></form></div></header>
<div class="shell">
<nav class="side" aria-label="Portal">
 <a href="#home" data-v="home">{{ icon('home')|safe }}Overview</a>
 <a href="#inbox" data-v="inbox">{{ icon('mail')|safe }}Inbox{% if unread %}<span class="badge"><span class="sr">: </span>{{ unread }} new</span>{% endif %}</a>
 <a href="#consultant" data-v="consultant">{{ icon('user')|safe }}Your Consultant</a>
 <a href="#legal" data-v="legal">{{ icon('doc')|safe }}Project docs</a>
 <a href="#invoices" data-v="invoices">{{ icon('card')|safe }}Invoices</a>
 <a href="#drive" data-v="drive">{{ icon('drive')|safe }}Your Drive</a>
 <a href="#pricing" data-v="pricing">{{ icon('tag')|safe }}Engagement & pricing</a>
 <a href="#roi" data-v="roi">{{ icon('calc')|safe }}ROI calculator</a>
 <a href="/work/" target="_blank" rel="noopener" class="ext">{{ icon('book')|safe }}Case studies<span class="sr">(opens in a new tab)</span>{{ icon('ext', 14)|safe }}</a>
 <a href="#settings" data-v="settings" class="sep">{{ icon('gear')|safe }}Settings</a>
</nav>
<main>
{% if note %}<p class="note" role="status">{{ note }}</p>{% endif %}

{% macro con_card() %}
 <div class="card con">
  {% if con.photo %}<img class="ph" src="/consultant-photo/{{ con.id }}" alt="{{ con.name }}" width="150" height="150">{% else %}<div class="ph" aria-hidden="true">{{ con.name[:1] }}</div>{% endif %}
  <div><h3>{{ con.name }}</h3><p class="t">{{ con.title }}</p><p class="bio">{{ con.bio }}</p>
   <div class="links">
    {% if con.email %}<a class="ib" href="mailto:{{ con.email }}">{{ icon('mail')|safe }}{{ con.email }}</a>{% endif %}
    {% if con.phone %}<a class="ib" href="tel:{{ con.phone|replace(' ','') }}">{{ icon('phone')|safe }}{{ con.phone }}</a>{% endif %}
    {% if con.linkedin %}<a class="ib" href="{{ con.linkedin }}" target="_blank" rel="noopener">{{ icon('in')|safe }}LinkedIn</a>{% endif %}
   </div>
   <p style="margin:22px 0 0"><a class="btn btn-p" href="{{ con.cal_url }}" target="_blank" rel="noopener">{{ icon('cal')|safe }}Book a meeting</a></p>
  </div>
 </div>
{% endmacro %}
<section class="view" id="v-home">
 <div class="hero slim"><div class="hero-top"><div><span class="label">Client portal</span><h1>Welcome, <span>{{ first }}</span></h1>
  {% if c.company %}<p class="co">{{ c.company }}</p>{% endif %}</div>
  {% if c.logo %}<div class="clogo"><img src="/portal/logo?v={{ c.logo }}" alt="{{ c.company }} logo"></div>{% endif %}</div>
  <div class="chips">{% if c.engagement %}<span class="chip"><b>{{ c.engagement }}</b></span>{% endif %}<span class="chip{% if c.status == 'Active' %} ok{% endif %}">{{ c.status or 'Active' }}</span></div>
  {% set cur = phases.index(c.phase) if c.phase in phases else 0 %}<ol class="phases" aria-label="Project progress">{% for ph in phases %}<li class="{{ 'done' if loop.index0 < cur else 'now' if loop.index0 == cur else '' }}"><a href="/portal/how-we-work{{ phase_links[ph] }}"{% if loop.index0 == cur %} aria-current="step"{% endif %}><span>{% if loop.first %}1 · {% endif %}{{ ph }}</span></a></li>{% endfor %}</ol>
  {% if cur > 0 %}<p class="hww"><a href="/portal/how-we-work">How we work →</a></p>{% endif %}
 </div>
 <div class="ov">
  <div class="ov-main">
   {% set sc = stage %}<div class="card intro"><span class="label">Step {{ cur + 1 }} · {{ 'Start here' if cur == 0 else phases[cur] }}</span>
    <h3>{{ sc.title }}</h3>
    <p>{{ sc.intro }}</p>
    <ol class="isteps">{% for b, t in sc.steps %}<li><b>{{ b }}</b> {{ t }}</li>{% endfor %}</ol>
    <div class="iact">{% for l, h, pr, ext in sc.actions %}<a class="btn {{ 'btn-p' if pr else 'btn-g' }}" href="{{ h }}"{% if ext %} target="_blank" rel="noopener"{% endif %}>{{ l }}</a>{% endfor %}</div>
   </div>
  </div>
  <aside class="ov-side"><div class="card cc"><span class="label">Your consultant</span>
   <div class="cc-h">{% if con.photo %}<img class="ph" src="/consultant-photo/{{ con.id }}" alt="" width="64" height="64">{% else %}<div class="ph" aria-hidden="true">{{ con.name[:1] }}</div>{% endif %}<div><b>{{ con.name }}</b><span>{{ con.title }}</span></div></div>
   <div class="cc-l">{% if con.email %}<a href="mailto:{{ con.email }}" aria-label="Email {{ con.name }}" title="{{ con.email }}">{{ icon('mail')|safe }}</a>{% endif %}{% if con.phone %}<a href="tel:{{ con.phone|replace(' ','') }}" aria-label="Call {{ con.name }}" title="{{ con.phone }}">{{ icon('phone')|safe }}</a>{% endif %}{% if con.linkedin %}<a href="{{ con.linkedin }}" target="_blank" rel="noopener" aria-label="{{ con.name }} on LinkedIn" title="LinkedIn">{{ icon('in')|safe }}</a>{% endif %}</div>
   <a class="btn btn-p" href="{{ con.cal_url }}" target="_blank" rel="noopener">{{ icon('cal')|safe }}Book a meeting</a>
   <a class="more" href="#consultant">About {{ con.name.split(' ')[0] }} →</a>
  </div>
  {% if c.consultant_note %}<div class="cnote" role="note" aria-label="Note from {{ con.name }}"><div class="nh">{% if con.photo %}<img class="av" src="/consultant-photo/{{ con.id }}" alt="" width="34" height="34">{% else %}<span class="av" aria-hidden="true">{{ con.name[:1] }}</span>{% endif %}<div><b>Note from {{ con.name.split(' ')[0] }}</b><span>Your consultant</span></div></div>
   <p>{{ c.consultant_note }}</p><a href="#inbox">Reply to {{ con.name.split(' ')[0] }} →</a></div>{% endif %}
  </aside>
 </div>
</section>

<section class="view" id="v-inbox" hidden>
 <div class="vh"><span class="label">Inbox</span><h2>Messages from your team</h2><p>Updates and requests from {{ con.name.split(' ')[0] }}. Reply here and it goes straight to us.</p></div>
 <div class="ibx">
  <div class="card ithread">{% if msgs %}<ol class="msgs">{% for m in msgs|reverse %}<li class="m {{ 'me' if m.sender == 'client' else 'them' }}{% if m.sender == 'consultant' and not m.read_client %} new{% endif %}">
    <div class="mh"><b>{{ 'You' if m.sender == 'client' else (m.author or con.name) }}</b><span>{{ m.at[:16]|replace('T',' ') }} UTC</span>{% if m.sender == 'consultant' and not m.read_client %}<span class="chip ok">New</span>{% endif %}</div>
    {% if m.subject %}<p class="sj">{{ m.subject }}</p>{% endif %}<div class="bd">{{ m.body }}</div></li>{% endfor %}</ol>
   {% else %}<div class="empty">No messages yet. When {{ con.name.split(' ')[0] }} sends you an update, it appears here.</div>{% endif %}</div>
  <form class="card fg" method="post" action="/portal/inbox"><h3 style="font-size:19px">Write to {{ con.name.split(' ')[0] }}</h3>
   <div class="f"><label for="m-subj">Subject (optional)</label><input id="m-subj" name="subject" maxlength="200"></div>
   <div class="f"><label for="m-body">Message</label><textarea id="m-body" name="body" required rows="6"></textarea></div>
   <div><button class="btn btn-p" type="submit">{{ icon('mail')|safe }}Send message</button></div></form>
 </div>
</section>

<section class="view" id="v-consultant" hidden>
 <div class="vh"><span class="label">Your Consultant</span><h2>Here to help, every step of the way</h2></div>
 {{ con_card() }}
</section>

<section class="view" id="v-legal" hidden>
 <div class="vh"><span class="label">Project docs</span><h2>Proposals, agreements and contracts</h2><p>Documents we've shared with {{ c.company }}. Select one to open it.</p></div>
 {% if legal %}{% for cat, items in legal|groupby('category') %}<div class="group"><h3>{{ cat or 'Other' }}</h3><div class="docs">
  {% for f in items %}<a class="doc" href="/portal/files/{{ f.id }}" target="_blank" rel="noopener"><span class="fi {{ (f.name|ext)|lower }}"><i>{{ f.name|ext }}</i></span><span class="n">{{ f.name }}</span><small>{{ f.at[:10] }} · {{ f.size|fsize }}</small></a>{% endfor %}
 </div></div>{% endfor %}{% else %}<div class="empty">No documents yet. Proposals and agreements will appear here when we share them.</div>{% endif %}
</section>

<section class="view" id="v-invoices" hidden>
 <div class="vh"><span class="label">Invoices</span><h2>Invoices and billing</h2><p>View, download and pay your invoices. Payments are handled securely by Stripe.</p></div>
 {% if not stripe or not c.stripe_customer_id %}<div class="empty">Billing isn't set up for your account yet. Your invoices will appear here once it is.</div>
 {% elif inv_err %}<div class="empty">Invoices can't be loaded right now. Please try again later.</div>
 {% else %}
  {% if inv %}<div class="tw"><table><thead><tr><th>Invoice</th><th>Date</th><th>Due</th><th>Amount</th><th>Status</th><th></th></tr></thead><tbody>
  {% for i in inv %}<tr><td>{{ i.number }}</td><td>{{ i.created }}</td><td>{{ i.due }}</td><td class="num">{{ i.amount }}</td><td><span class="st {{ i.status }}">{{ i.status }}</span></td>
  <td>{% if i.url %}<a href="{{ i.url }}" target="_blank" rel="noopener">{{ 'Pay now' if i.status == 'open' else 'View' }}</a>{% endif %}{% if i.pdf %} · <a href="{{ i.pdf }}" target="_blank" rel="noopener">PDF</a>{% endif %}</td></tr>{% endfor %}
  </tbody></table></div>{% else %}<div class="empty">No invoices yet.</div>{% endif %}
  <form method="post" action="/portal/billing" style="margin-top:18px"><button class="btn btn-g" type="submit">{{ icon('card')|safe }}Manage billing details and payment methods</button></form>
 {% endif %}
</section>

<section class="view" id="v-drive" hidden>
 <div class="vh"><span class="label">Your Drive</span><h2>Share files with our team</h2><p>Upload anything we should see: data exports, briefs, templates. Up to 25 MB per file.</p></div>
 <div class="drop" tabindex="0" role="button">{{ icon('drive', 30)|safe }}<br><b>Drop files here</b> or click to choose</div>
 <input type="file" id="picker" multiple hidden>
 {% if drive %}<div class="docs">{% for f in drive %}<a class="doc" href="/portal/files/{{ f.id }}" target="_blank" rel="noopener"><span class="fi {{ (f.name|ext)|lower }}"><i>{{ f.name|ext }}</i></span><span class="n">{{ f.name }}</span><small>{{ 'You' if f.uploaded_by == 'client' else 'Automagic' }} · {{ f.at[:10] }}</small>{% if f.uploaded_by == 'client' %}<button class="x" type="button" data-del="{{ f.id }}" aria-label="Delete {{ f.name }}">×</button>{% endif %}</a>{% endfor %}</div>
 {% else %}<div class="empty">Your drive is empty.</div>{% endif %}
</section>

<section class="view" id="v-pricing" hidden>
 <div class="vh"><span class="label">{{ pr.label }}</span><h2>{{ pr.heading }}</h2>{% if pr.intro %}<p>{{ pr.intro }}</p>{% endif %}</div>
 {% set prin = pr.principles|selectattr('title')|list %}{% if prin %}<ul class="prin">
  {% for x in prin %}<li>{{ icon('check')|safe }}<span><b>{{ x.title }}</b> {{ x.text }}</span></li>{% endfor %}
 </ul>{% endif %}
 {% set items = pr['items']|selectattr('title')|list %}<div class="pm">
  {% for x in items %}<article class="pc{% if x.outline %} ext{% endif %}"><div class="pt"><span class="pn">{{ '%02d' % loop.index }}</span>{% if x.badge %}<span class="pu">{{ x.badge }}</span>{% endif %}</div><h3>{{ x.title }}</h3>
   {% if x.price %}<p class="pp">{% if x.prefix %}{{ x.prefix }} {% endif %}<b>{{ x.price }}</b>{% if x.unit %} <small>{{ x.unit }}</small>{% endif %}</p>{% endif %}
   {% if x.text %}<p>{{ x.text }}</p>{% endif %}
   {% if x.link_url and x.link_label %}<a class="pl" href="{{ x.link_url }}" target="_blank" rel="noopener">{{ x.link_label }} ↗</a>{% endif %}</article>{% endfor %}
 </div>
 {% set flow = pr.flow|selectattr('title')|list %}<div class="card pflow">{% if flow %}<span class="label">{{ pr.flow_title }}</span>
  <ol>{% for x in flow %}<li><b>{{ x.title }}</b><span>{{ x.text }}</span></li>{% endfor %}</ol>{% endif %}
  <p>{% if pr.note %}{{ pr.note }} {% endif %}{% if legal %}See <a href="#legal">Project docs</a>. {% endif %}Questions? <a href="mailto:{{ con.email or 'hello@automagic.agency' }}">Ask {{ con.name.split(' ')[0] }}</a>.</p>
 </div>
</section>

<section class="view" id="v-roi" hidden>
 <div class="vh"><span class="label">ROI calculator</span><h2>Estimate your return</h2><p>Adjust the numbers to match your team. Everything is calculated in your browser and nothing is saved or sent.</p></div>
 <div class="roi">
  <form class="card fg" id="roi-f">
   <h3 class="rh">Time saved</h3>
   <div class="f"><label for="r-people">People whose work changes</label><input id="r-people" type="number" min="0" step="1" value="12" inputmode="numeric"></div>
   <div class="f"><label for="r-hours">Hours saved per person, per week</label><input id="r-hours" type="number" min="0" step="0.5" value="5" inputmode="decimal"></div>
   <div class="f"><label for="r-rate">Loaded cost per hour ($)</label><input id="r-rate" type="number" min="0" step="1" value="90" inputmode="decimal"></div>
   <div class="f"><label for="r-adopt">Adoption rate: <output id="r-adopt-o">70%</output></label><input id="r-adopt" type="range" min="10" max="100" step="5" value="70"></div>
   <div class="f"><label for="r-rev">Extra revenue per year, optional ($)</label><input id="r-rev" type="number" min="0" step="1000" value="0" inputmode="decimal"></div>
   <h3 class="rh">Investment</h3>
   <div class="f"><label for="r-once">One-off: discovery and builds ($)</label><input id="r-once" type="number" min="0" step="1000" value="60000" inputmode="decimal"></div>
   <div class="f"><label for="r-month">Monthly: lead, consultancy and external costs ($)</label><input id="r-month" type="number" min="0" step="500" value="6000" inputmode="decimal"></div>
  </form>
  <div class="fg">
   <div class="kpis">
    <div class="kpi hi"><span>Return on investment, year 1</span><b id="o-roi">–</b></div>
    <div class="kpi"><span>Payback</span><b id="o-pay">–</b></div>
    <div class="kpi"><span>Value created per year</span><b id="o-val">–</b></div>
    <div class="kpi"><span>Hours freed per year</span><b id="o-hrs">–</b></div>
   </div>
   <div class="card"><h3 class="rh" style="margin-top:0">Three-year view</h3>
    <div class="tw"><table><thead><tr><th>Year</th><th>Value</th><th>Cost</th><th>Net</th><th>Cumulative</th></tr></thead><tbody id="o-tbl"></tbody></table></div>
    <p class="rnote">Assumes 46 working weeks a year and that the one-off cost falls in year 1. This is an estimate to frame the conversation, not a quote. Your consultant can build a detailed business case with you during discovery.</p>
   </div>
  </div>
 </div>
</section>

<section class="view" id="v-settings" hidden>
 <div class="vh"><span class="label">Settings</span><h2>Your profile and account</h2></div>
 <div class="grid2">
  <form class="card fg" method="post" action="/portal/settings"><input type="hidden" name="action" value="profile"><h3 style="font-size:19px">Profile</h3>
   <div class="f"><label for="s-name">Full name</label><input id="s-name" name="contact_name" value="{{ c.contact_name or '' }}"></div>
   <div class="f"><label for="s-title">Job title</label><input id="s-title" name="contact_title" value="{{ c.contact_title or '' }}"></div>
   <div class="f"><label for="s-phone">Phone</label><input id="s-phone" name="contact_phone" type="tel" value="{{ c.contact_phone or '' }}"></div>
   <div class="f"><label for="s-li">LinkedIn</label><input id="s-li" name="contact_linkedin" type="url" value="{{ c.contact_linkedin or '' }}"></div>
   <div class="f"><label for="s-email">Login email</label><input id="s-email" value="{{ c.contact_email }}" readonly></div>
   <div><button class="btn btn-p" type="submit">Save profile</button></div></form>
  <div class="fg">
   <div class="card"><h3 style="font-size:19px;margin-bottom:14px">Company</h3><dl class="kv">
    <dt>Company</dt><dd>{{ c.company }}</dd>{% if c.website %}<dt>Website</dt><dd>{{ c.website }}</dd>{% endif %}{% if c.industry %}<dt>Industry</dt><dd>{{ c.industry }}</dd>{% endif %}
    {% if c.country %}<dt>Country</dt><dd>{{ c.country }}</dd>{% endif %}{% if c.engagement %}<dt>Engagement</dt><dd>{{ c.engagement }}</dd>{% endif %}</dl>
    <p style="color:var(--faint);font-size:13.5px;margin:14px 0 0">To change company details, email {{ con.email }}.</p></div>
   <form class="card fg" method="post" action="/portal/settings"><input type="hidden" name="action" value="password"><h3 style="font-size:19px">Change password</h3>
    <div class="f"><label for="p-cur">Current password</label><input id="p-cur" name="current" type="password" autocomplete="current-password" required></div>
    <div class="f"><label for="p-new">New password (10+ characters)</label><input id="p-new" name="new" type="password" autocomplete="new-password" minlength="10" required></div>
    <div class="f"><label for="p-con">Confirm new password</label><input id="p-con" name="confirm" type="password" autocomplete="new-password" minlength="10" required></div>
    <div><button class="btn btn-p" type="submit">Change password</button></div></form>
   <form class="card" method="post" action="/portal/settings"><input type="hidden" name="action" value="signout_all"><h3 style="font-size:19px;margin-bottom:8px">Sessions</h3>
    <p style="color:var(--muted);font-size:14px;margin:0 0 14px">Signed in on a shared or lost device? Sign out everywhere except here.</p><button class="btn btn-g" type="submit">Sign out of other devices</button></form>
  </div>
 </div>
</section>
</main></div>
<footer class="foot"><div class="in">
 <span>© 2026 Automagic AI</span>
 <nav aria-label="Footer"><a href="/" target="_blank" rel="noopener">Website</a><a href="/work/" target="_blank" rel="noopener">Case studies</a><a href="/terms/" target="_blank" rel="noopener">Terms and privacy</a>{% if con.email %}<a href="mailto:{{ con.email }}">Contact {{ con.name.split(' ')[0] }}</a>{% endif %}</nav>
 <span class="help">Need help? <a href="mailto:{{ con.email or 'hello@automagic.agency' }}">{{ con.email or 'hello@automagic.agency' }}</a></span>
</div></footer>
<script>
(function(){
  var views=['home','inbox','consultant','legal','invoices','drive','pricing','roi','settings'];
  function show(){var v=(location.hash||'#home').slice(1); if(views.indexOf(v)<0) v='home';
    views.forEach(function(n){document.getElementById('v-'+n).hidden=n!==v});
    document.querySelectorAll('.side a').forEach(function(a){a.classList.toggle('on',a.dataset.v===v); if(a.dataset.v===v) a.setAttribute('aria-current','page'); else a.removeAttribute('aria-current')});
    window.scrollTo(0,0);}
  var unread={{ unread|tojson }};
  function markRead(){ if(unread && location.hash==='#inbox'){ unread=0; fetch('/portal/api/inbox/read',{method:'POST',credentials:'same-origin'}).then(function(){ document.querySelectorAll('.side .badge').forEach(function(b){b.remove()}); }); } }
  window.addEventListener('hashchange',function(){show();markRead()}); show(); markRead();
  var rf=document.getElementById('roi-f');
  if(rf){ var WK=46, fmt=function(n){return (n<0?'-$':'$')+Math.round(Math.abs(n)).toLocaleString('en-US')};
    var g=function(id){var v=parseFloat(document.getElementById(id).value);return isFinite(v)&&v>0?v:0};
    function calc(){ var ad=g('r-adopt')/100; document.getElementById('r-adopt-o').textContent=Math.round(ad*100)+'%';
      var hrs=g('r-people')*g('r-hours')*WK*ad, val=hrs*g('r-rate')+g('r-rev'), once=g('r-once'), mo=g('r-month'), run=mo*12;
      var c1=once+run, roi=c1>0?(val-c1)/c1*100:0, net=val/12-mo;
      document.getElementById('o-hrs').textContent=Math.round(hrs).toLocaleString('en-US');
      document.getElementById('o-val').textContent=fmt(val);
      document.getElementById('o-roi').textContent=c1>0?Math.round(roi)+'%':'–';
      var pay=net>0?once/net:Infinity; document.getElementById('o-pay').textContent=net<=0?'Not reached':pay<1?'Under 1 month':pay>36?'Over 3 years':Math.ceil(pay)+' months';
      var cum=0,rows='';for(var y=1;y<=3;y++){var c=run+(y===1?once:0),n=val-c;cum+=n;
        rows+='<tr><td>Year '+y+'</td><td class="num">'+fmt(val)+'</td><td class="num">'+fmt(c)+'</td><td class="num'+(n<0?' neg':'')+'">'+fmt(n)+'</td><td class="num'+(cum<0?' neg':'')+'">'+fmt(cum)+'</td></tr>';}
      document.getElementById('o-tbl').innerHTML=rows; }
    rf.addEventListener("submit",function(e){e.preventDefault()}); rf.addEventListener("input",calc); calc(); }
  var picker=document.getElementById('picker'), active=null;
  function up(files, zone){ if(!files||!files.length) return; var fd=new FormData(); for(var i=0;i<files.length;i++) fd.append('file',files[i]);
    zone.innerHTML='<b>Uploading…</b>';
    fetch('/portal/api/drive',{method:'POST',body:fd,credentials:'same-origin'}).then(function(r){return r.json()})
     .then(function(j){ if(j.errors&&j.errors.length){ zone.innerHTML='<b>'+j.errors.join('<br>').replace(/</g,'&lt;')+'</b>'; setTimeout(function(){location.reload()},2500);} else location.reload(); })
     .catch(function(){ zone.innerHTML='<b>Upload failed.</b> Please try again.'; });}
  document.querySelectorAll('.drop').forEach(function(drop){
    drop.addEventListener('click',function(){active=drop;picker.click()});
    drop.addEventListener('keydown',function(e){if(e.key==='Enter'||e.key===' '){e.preventDefault();active=drop;picker.click()}});
    drop.addEventListener('dragover',function(e){e.preventDefault();drop.classList.add('over')});
    drop.addEventListener('dragleave',function(){drop.classList.remove('over')});
    drop.addEventListener('drop',function(e){e.preventDefault();drop.classList.remove('over');up(e.dataTransfer.files,drop)});
  });
  picker.addEventListener('change',function(){up(picker.files,active||document.querySelector('.drop'));picker.value=''});
  document.querySelectorAll('.x[data-del]').forEach(function(b){b.addEventListener('click',function(e){e.preventDefault();e.stopPropagation();
    if(!b.classList.contains('armed')){b.classList.add('armed');b.textContent='Delete?';return}
    fetch('/portal/api/drive/'+b.dataset.del+'/delete',{method:'POST',credentials:'same-origin'}).then(function(){location.reload()});});});
})();
</script>
</body></html>"""
