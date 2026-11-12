"""Admin pages that sit in the left menu next to Clients and Consultants (portal.py):

  /admin/inbox      everything new: client messages, Book a Call requests, client drive uploads, portfolio sign-ups
  /admin            analytics: see analytics.py (site-wide, per page, per visitor journeys)
  /admin/leads      Lead CRM: Book a Call leads with a pipeline status, private notes, convert to client
  /admin/visitors   portfolio access emails and visitor activity by email

init() also seeds one demo consultant and one demo client the first time it runs.
"""
import os, secrets
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, redirect, render_template_string, request, session
from werkzeug.security import generate_password_hash

import admin_ui as U
import portal as P
import server as S

bp = Blueprint("admin", __name__)

LEAD_STATUSES = ["New", "Contacted", "Qualified", "Proposal sent", "Won", "Lost"]
LEAD_FIELDS = ["name", "company", "website", "email", "engagement", "notes"]


def add_col(table, col, typ):
    if S.DATABASE_URL:
        S.q(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {typ}")
    elif col not in [r[1] for r in S.q(f"PRAGMA table_info({table})")]:
        S.q(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")


def init():
    with S.app.app_context():
        for col, typ in (("status", "TEXT"), ("admin_notes", "TEXT"), ("updated", "TEXT"), ("client_id", "INTEGER")):
            add_col("leads", col, typ)
        seed_demo()
        P.commit()


# ---------------------------------------------------------------- demo data
DEMO_PDF = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj
4 0 obj<</Length 178>>stream
BT /F1 22 Tf 60 760 Td (Northwind Logistics - Discovery proposal) Tj /F1 12 Tf 0 -36 Td (Demo document created by Automagic for the client portal demo.) Tj 0 -20 Td (Safe to delete.) Tj ET
endstream endobj
5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
trailer<</Root 1 0 R>>
%%EOF
"""


def _store(cid, name, data, kind, category, by):
    folder = os.path.join(P.FILES_DIR, str(int(cid)))
    os.makedirs(folder, exist_ok=True)
    stored = secrets.token_hex(16) + os.path.splitext(name)[1].lower()
    with open(os.path.join(folder, stored), "wb") as out:
        out.write(data)
    mime = "application/pdf" if name.endswith(".pdf") else "text/plain"
    S.q("INSERT INTO client_files (client_id, at, kind, category, name, stored, size, mime, uploaded_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (cid, S.now(), kind, category, name, stored, len(data), mime, by))


def seed_demo():
    """Create a demo consultant and a demo client once. Deleting them later doesn't bring them back."""
    if S.q("SELECT v FROM settings WHERE k = 'demo.seeded'", one=True):
        return
    con = {"name": "Alex Rivera", "title": "AI Transformation Consultant (demo profile)", "email": "alex.rivera@example.com",
           "phone": "+44 20 7946 0123", "linkedin": "https://www.linkedin.com/", "cal_url": P.CAL_URL,
           "bio": "Alex is a demo consultant, created so you can see how a consultant profile looks in the client portal. "
                  "Assign a real consultant to each client from the Clients page, then delete this profile."}
    S.q(f"INSERT INTO consultants (created, updated, {', '.join(P.CON_FIELDS)}, photo) VALUES (?, ?, {', '.join('?' * len(P.CON_FIELDS))}, '')",
        (S.now(), S.now(), *[con[k] for k in P.CON_FIELDS]))
    pid = S.q("SELECT id FROM consultants ORDER BY id DESC", one=True)[0]
    email = "demo.client@example.com"
    if not S.q("SELECT id FROM clients WHERE contact_email = ?", (email,), one=True):
        c = {k: "" for k in P.CLIENT_FIELDS} | {
            "company": "Northwind Logistics (Demo)", "website": "northwind.example", "industry": "Logistics",
            "country": "United Kingdom", "address": "1 Demo Street, London", "status": "Active", "engagement": "AI Transformation",
            "contact_name": "Jordan Lee", "contact_title": "Chief Operating Officer", "contact_email": email,
            "contact_phone": "+44 20 7946 0456", "contact_linkedin": "https://www.linkedin.com/",
            "notes": "Demo client, created automatically to show the CRM and the client portal. Open the client page, "
                     "tick Generate a password and save to sign in at /login/ as this client. Safe to delete."}
        S.q(f"INSERT INTO clients (created, updated, {', '.join(P.CLIENT_FIELDS)}, portal_enabled, consultant_id, password_hash) "
            f"VALUES (?, ?, {', '.join('?' * len(P.CLIENT_FIELDS))}, 1, ?, ?)",
            (S.now(), S.now(), *[c[k] for k in P.CLIENT_FIELDS], pid, generate_password_hash(secrets.token_urlsafe(24))))
        cid = S.q("SELECT id FROM clients WHERE contact_email = ?", (email,), one=True)[0]
        _store(cid, "Discovery proposal - Northwind (demo).pdf", DEMO_PDF, "legal", "Proposal", "admin")
        _store(cid, "Current process notes (demo).txt",
               b"Demo file uploaded by the demo client.\nOrders arrive by email and are keyed into the TMS by hand.\n",
               "drive", None, "client")
    P.set_setting("demo.seeded", S.now())


# ---------------------------------------------------------------- menu badges
def seen():
    r = S.q("SELECT v FROM settings WHERE k = 'inbox.seen'", one=True)
    return (r[0] if r else "") or ""


@bp.app_context_processor
def admin_nav():
    if not (session.get("admin") and request.path.startswith("/admin")):
        return {"admin_nav": {}}
    t = seen()
    unread = (S.q("SELECT COUNT(*) FROM leads WHERE at > ?", (t,), one=True)[0]
              + S.q("SELECT COUNT(*) FROM access WHERE at > ?", (t,), one=True)[0]
              + S.q("SELECT COUNT(*) FROM client_files WHERE uploaded_by = 'client' AND at > ?", (t,), one=True)[0]
              + S.q("SELECT COUNT(*) FROM messages WHERE sender = 'client' AND at > ? AND read_admin IS NULL", (t,), one=True)[0])
    new_leads = S.q("SELECT COUNT(*) FROM leads WHERE COALESCE(status, 'New') = 'New'", one=True)[0]
    msgs = S.q("SELECT COUNT(*) FROM messages WHERE sender = 'client' AND read_admin IS NULL", one=True)[0]
    return {"admin_nav": {"inbox": unread, "leads": new_leads, "clients": msgs}}


def guard():
    return S.admin_required()


# ---------------------------------------------------------------- inbox
INBOX_TYPES = {"messages": "Client messages", "leads": "Book a Call", "uploads": "Client uploads", "access": "Portfolio sign-ups"}


@bp.get("/admin/inbox")
def inbox():
    r = guard()
    if r:
        return r
    t, kind = seen(), request.args.get("type", "")
    items = []
    if kind in ("", "leads"):
        for lid, at, name, company, eng, notes in S.q("SELECT id, at, name, company, engagement, notes FROM leads ORDER BY at DESC LIMIT 200"):
            items.append({"at": at, "icon": "lead", "href": f"/admin/leads/{lid}", "title": f"Book a Call request from {name}" + (f", {company}" if company else ""),
                          "sub": " · ".join(x for x in (eng, (notes or "").replace("\n", " ")[:140]) if x) or "No message"})
    if kind in ("", "messages"):
        for cid, at, sender, author, subj, body, read, company in S.q(
                "SELECT m.client_id, m.at, m.sender, m.author, m.subject, m.body, m.read_admin, c.company FROM messages m "
                "JOIN clients c ON c.id = m.client_id ORDER BY m.at DESC LIMIT 200"):
            mine = sender == "consultant"
            title = (f"{author or 'You'} messaged {company}" if mine else f"Message from {author}, {company}" if author else f"Message from {company}")
            items.append({"at": at, "icon": "sent" if mine else "msg", "href": f"/admin/clients/{cid}/inbox#latest", "title": title,
                          "sub": " · ".join(x for x in (subj, (body or "").replace("\n", " ")[:140]) if x), "fresh": not mine and not read})
    if kind in ("", "uploads"):
        for cid, at, fname, company in S.q("SELECT f.client_id, f.at, f.name, c.company FROM client_files f JOIN clients c ON c.id = f.client_id "
                                           "WHERE f.uploaded_by = 'client' ORDER BY f.at DESC LIMIT 200"):
            items.append({"at": at, "icon": "upload", "href": f"/admin/clients/{cid}", "title": f"{company} uploaded {fname}", "sub": "Your Drive, client portal"})
    if kind in ("", "access"):
        for at, email, page, country, city in S.q("SELECT at, email, page, country, city FROM access ORDER BY at DESC LIMIT 200"):
            items.append({"at": at, "icon": "key", "href": "/admin/visitors", "title": f"{email} signed up to view the portfolio",
                          "sub": " · ".join(x for x in (page and f"Asked for {page}", ", ".join(y for y in (city, country) if y)) if x) or "Portfolio access"})
    items.sort(key=lambda i: i["at"], reverse=True)
    for i in items:
        i["unread"] = i.get("fresh", True) and i["at"] > t
    return render_template_string(INBOX, items=items[:200], kind=kind, types=INBOX_TYPES, note=request.args.get("note", ""))


@bp.route("/admin/inbox/compose", methods=["GET", "POST"])
def inbox_compose():
    r = guard()
    if r:
        if request.method == "POST":
            abort(401)
        return r
    clients = [{"id": i, "company": co, "name": n, "portal": bool(p), "con": P.consultant_for(P.client_row(i))["name"]}
               for i, co, n, p in S.q("SELECT id, company, contact_name, portal_enabled FROM clients ORDER BY company")]
    f, err = request.form, ""
    sel = request.values.get("client_id", type=int)
    if request.method == "POST":
        body = S.clip(f.get("body"), 10000).strip()
        c = P.client_row(sel) if sel else None
        if not c:
            err = "Choose a client."
        elif not body:
            err = "Write a message first."
        else:
            author = S.clip(f.get("author"), 200) or P.consultant_for(c)["name"]
            P.add_message(sel, "consultant", author, S.clip(f.get("subject"), 200), body)
            P.commit()
            return redirect(f"/admin/clients/{sel}/inbox?sent=1#latest")
    return render_template_string(COMPOSE, clients=clients, sel=sel, err=err, f=f)


@bp.post("/admin/inbox/read")
def inbox_read():
    r = guard()
    if r:
        return r
    P.set_setting("inbox.seen", S.now())
    P.commit()
    return redirect("/admin/inbox")


# ---------------------------------------------------------------- analytics
# ---------------------------------------------------------------- lead CRM
LEAD_KEYS = ["id", "at", "name", "company", "website", "email", "engagement", "notes", "page", "access_email",
             "status", "admin_notes", "updated", "client_id"]


def lead_row(lid):
    r = S.q(f"SELECT {', '.join(LEAD_KEYS)} FROM leads WHERE id = ?", (lid,), one=True)
    if not r:
        return None
    d = dict(zip(LEAD_KEYS, r))
    d["status"] = d["status"] or "New"
    return d


@bp.get("/admin/leads")
def leads():
    r = guard()
    if r:
        return r
    status, qtext = request.args.get("status", ""), S.clip(request.args.get("q"), 100)
    counts = defaultdict(int)
    for st, n in S.q("SELECT COALESCE(status, 'New'), COUNT(*) FROM leads GROUP BY COALESCE(status, 'New')"):
        counts[st] += n
    sql, args = "SELECT id, at, name, company, email, engagement, COALESCE(status, 'New'), updated, client_id FROM leads WHERE 1 = 1", []
    if status in LEAD_STATUSES:
        sql += " AND COALESCE(status, 'New') = ?"
        args.append(status)
    if qtext:
        sql += " AND (LOWER(name) LIKE ? OR LOWER(company) LIKE ? OR LOWER(email) LIKE ?)"
        args += [f"%{qtext.lower()}%"] * 3
    rows = S.q(sql + " ORDER BY at DESC LIMIT 500", tuple(args))
    return render_template_string(LEADS, rows=rows, counts=counts, total=sum(counts.values()), statuses=LEAD_STATUSES,
                                  status=status, qtext=qtext, note="Lead deleted." if request.args.get("deleted") else "")


@bp.route("/admin/leads/new", methods=["GET", "POST"])
@bp.route("/admin/leads/<int:lid>", methods=["GET", "POST"])
def lead(lid=None):
    r = guard()
    if r:
        return r
    d = lead_row(lid) if lid else {k: "" for k in LEAD_KEYS} | {"id": None, "status": "New", "client_id": None}
    if lid and not d:
        abort(404)
    err, note = "", "Saved." if request.args.get("saved") else ""
    if request.method == "POST":
        f = request.form
        vals = {k: S.clip(f.get(k), 4000 if k == "notes" else 300) for k in LEAD_FIELDS}
        vals["email"] = vals["email"].lower()
        vals["engagement"] = vals["engagement"] if vals["engagement"] in S.TYPES else ""
        st = f.get("status") if f.get("status") in LEAD_STATUSES else "New"
        admin_notes = S.clip(f.get("admin_notes"), 8000)
        d.update(vals, status=st, admin_notes=admin_notes)
        if not vals["name"]:
            err = "Add the lead's name."
        elif not S.EMAIL_RE.match(vals["email"]):
            err = "Add a valid email address."
        else:
            if lid:
                S.q(f"UPDATE leads SET {', '.join(k + ' = ?' for k in LEAD_FIELDS)}, status = ?, admin_notes = ?, updated = ? WHERE id = ?",
                    (*[vals[k] for k in LEAD_FIELDS], st, admin_notes, S.now(), lid))
            else:
                S.q(f"INSERT INTO leads (at, {', '.join(LEAD_FIELDS)}, page, status, admin_notes, updated) VALUES (?, {', '.join('?' * len(LEAD_FIELDS))}, ?, ?, ?, ?)",
                    (S.now(), *[vals[k] for k in LEAD_FIELDS], "Added in admin", st, admin_notes, S.now()))
                lid = S.q("SELECT id FROM leads ORDER BY id DESC", one=True)[0]
            P.commit()
            return redirect(f"/admin/leads/{lid}?saved=1")
    client = P.client_row(d["client_id"]) if d.get("client_id") else None
    match = None
    if not client and d.get("email"):
        m = S.q("SELECT id, company FROM clients WHERE contact_email = ?", (d["email"],), one=True)
        match = {"id": m[0], "company": m[1]} if m else None
    return render_template_string(LEAD, d=d, lid=lid, err=err, note=note, statuses=LEAD_STATUSES, types=S.TYPES,
                                  client=client, match=match)


@bp.post("/admin/leads/<int:lid>/convert")
def lead_convert(lid):
    r = guard()
    if r:
        return r
    d = lead_row(lid)
    if not d:
        abort(404)
    m = S.q("SELECT id FROM clients WHERE contact_email = ?", (d["email"],), one=True) if d["email"] else None
    if m:
        cid = m[0]
    else:
        c = {k: "" for k in P.CLIENT_FIELDS} | {
            "company": d["company"] or d["name"], "website": d["website"] or "", "status": "Introduction",
            "engagement": d["engagement"] if d["engagement"] in S.TYPES else "", "contact_name": d["name"],
            "contact_email": d["email"] or None,
            "notes": "\n\n".join(x for x in (d["notes"] and "From the Book a Call form:\n" + d["notes"], d["admin_notes"]) if x)}
        S.q(f"INSERT INTO clients (created, updated, {', '.join(P.CLIENT_FIELDS)}, portal_enabled) VALUES (?, ?, {', '.join('?' * len(P.CLIENT_FIELDS))}, 0)",
            (S.now(), S.now(), *[c[k] for k in P.CLIENT_FIELDS]))
        cid = S.q("SELECT id FROM clients ORDER BY id DESC", one=True)[0]
    S.q("UPDATE leads SET status = 'Won', client_id = ?, updated = ? WHERE id = ?", (cid, S.now(), lid))
    P.commit()
    serr = P.ensure_stripe_customer(cid)
    if serr:
        return redirect(f"/admin/clients/{cid}?saved=1&stripe_error=" + P.urllib.parse.quote("client created, but the Stripe customer wasn't: " + serr))
    return redirect(f"/admin/clients/{cid}?saved=1")


@bp.post("/admin/leads/<int:lid>/delete")
def lead_delete(lid):
    r = guard()
    if r:
        return r
    if request.form.get("confirm") != "DELETE":
        return redirect(f"/admin/leads/{lid}")
    S.q("DELETE FROM leads WHERE id = ?", (lid,))
    P.commit()
    return redirect("/admin/leads?deleted=1")


# ---------------------------------------------------------------- access and visitors
@bp.get("/admin/visitors")
def visitors():
    r = guard()
    if r:
        return r
    access = S.q("SELECT at, email, page, country, city FROM access ORDER BY at DESC LIMIT 500")
    people = S.q("SELECT email, COUNT(*), MAX(at), COUNT(DISTINCT path) FROM visits WHERE email IS NOT NULL GROUP BY email ORDER BY MAX(at) DESC LIMIT 200")
    last_place = {}
    for email, country, city in S.q("SELECT email, country, city FROM visits WHERE email IS NOT NULL AND country IS NOT NULL ORDER BY at"):
        last_place[email] = ", ".join(x for x in (city, country) if x)
    import analytics as A
    return render_template_string(VISITORS, access=access, people=people, last_place=last_place,
                                  spent=A.people_time(), dur=A.dur,
                                  tot_access=S.q("SELECT COUNT(*), COUNT(DISTINCT email) FROM access", one=True))


# ---------------------------------------------------------------- templates
FOOT = U.FOOT

INBOX = U.head("inbox", "Inbox") + """
<div class="ph"><div><h2>Inbox</h2><p>Client messages, Book a Call requests, files your clients upload and portfolio sign-ups, newest first.</p></div>
<div class="row">{% if admin_nav.inbox %}<form method="post" action="/admin/inbox/read"><button class="ghost" type="submit">Mark all as read</button></form>{% endif %}<a class="btn" href="/admin/inbox/compose">""" + U.ico("sent", 16) + """ Compose</a></div></div>
<nav class="chips" aria-label="Filter"><a href="/admin/inbox" class="{{ 'on' if not kind }}">All</a>{% for k, label in types.items() %}<a href="/admin/inbox?type={{ k }}" class="{{ 'on' if kind == k }}">{{ label }}</a>{% endfor %}</nav>
{% if items %}<ul class="feed">{% for i in items %}<li class="{{ 'un' if i.unread }}"><a class="fi" href="{{ i.href }}"><span class="ico">{{ ico(i.icon)|safe }}</span><span class="tx"><b>{{ i.title }}</b><span>{{ i.sub }}</span></span><time datetime="{{ i.at }}">{{ i.at[:16]|replace('T',' ') }}</time></a></li>{% endfor %}</ul>
{% else %}<div class="card empty">Nothing here yet. New client messages, Book a Call requests, client uploads and portfolio sign-ups will appear here.</div>{% endif %}
<p class="mono" style="margin-top:14px;text-transform:none;letter-spacing:0">Times are UTC.</p>
""" + FOOT

COMPOSE = U.head("inbox", "New message") + """
<p><a href="/admin/inbox">← Inbox</a></p>
<div class="ph"><div><h2>New message</h2><p>Sent to the client's portal Inbox and kept in their client record.</p></div></div>
{% if err %}<p class="err" role="alert">{{ err }}</p>{% endif %}
{% if clients %}<form class="card" method="post" style="max-width:760px;display:grid;gap:14px">
 <div><label class="f" for="client_id">To</label><select id="client_id" name="client_id" required><option value="">Choose a client…</option>
  {% for c in clients %}<option value="{{ c.id }}" data-con="{{ c.con }}"{% if sel == c.id %} selected{% endif %}>{{ c.company }}{% if c.name %} · {{ c.name }}{% endif %}{% if not c.portal %} (portal off){% endif %}</option>{% endfor %}</select></div>
 <div><label class="f" for="author">From</label><input type="text" id="author" name="author" value="{{ f.get('author', '') }}" placeholder="The client's consultant"></div>
 <div><label class="f" for="subject">Subject (optional)</label><input type="text" id="subject" name="subject" maxlength="200" value="{{ f.get('subject', '') }}"></div>
 <div><label class="f" for="body">Message</label><textarea id="body" name="body" required style="min-height:220px">{{ f.get('body', '') }}</textarea></div>
 <div class="row"><button type="submit">Send to client</button><a class="btn ghost" href="/admin/inbox">Cancel</a></div>
</form>
<script>(function(){var s=document.getElementById('client_id'),a=document.getElementById('author');
function fill(){var o=s.options[s.selectedIndex];if(o&&o.dataset.con&&(!a.value||a.dataset.auto==='1')){a.value=o.dataset.con;a.dataset.auto='1'}}
a.addEventListener('input',function(){a.dataset.auto='0'});s.addEventListener('change',fill);fill();})();</script>
{% else %}<div class="card empty">No clients yet. Add a client first, then you can message them.</div>{% endif %}
""" + FOOT

VISITORS = U.head("visitors", "Access and visitors") + """
<div class="ph"><div><h2>Access and visitors</h2><p>People who signed up with their email to view the portfolio, and what they looked at.</p></div></div>
<section id="access"><h2 style="font-size:19px">Access emails <span class="pill">{{ tot_access[1] }}</span> <a href="/admin/export/access.csv">Download CSV</a></h2>
{% if access %}<div class="tw"><table><thead><tr><th>Signed up (UTC)</th><th>Email</th><th>First page asked for</th><th>Location</th></tr></thead><tbody>
{% for at, email, page, country, city in access %}<tr><td class="nw">{{ at[:16]|replace('T',' ') }}</td><td><a href="mailto:{{ email }}">{{ email }}</a></td><td>{{ page }}</td><td>{{ [city, country]|select|join(', ') }}</td></tr>{% endfor %}
</tbody></table></div>{% else %}<div class="card empty">No sign-ups yet.</div>{% endif %}</section>
<section id="people"><h2 style="font-size:19px">Visitor activity by email</h2><p style="color:var(--muted);font-size:14px;margin:-6px 0 12px">Click someone to see their journey: every visit, page, time spent and how far they read.</p>
{% if people %}<div class="tw"><table><thead><tr><th>Email</th><th>Page views</th><th>Pages seen</th><th>Time spent</th><th>Last seen (UTC)</th><th>Location</th></tr></thead><tbody>
{% for email, n, last, distinct in people %}<tr class="rowlink" onclick="location.href=this.querySelector('a').href"><td><a href="/admin/visitors/journey?email={{ email|urlencode }}">{{ email }}</a></td><td>{{ n }}</td><td>{{ distinct }}</td><td>{{ dur(spent.get(email)) if spent.get(email) else '–' }}</td><td class="nw">{{ last[:16]|replace('T',' ') }}</td><td>{{ last_place.get(email, '') }}</td></tr>{% endfor %}
</tbody></table></div>{% else %}<div class="card empty">No signed-in visits yet.</div>{% endif %}</section>
""" + FOOT


@bp.app_template_global("ico")
def _ico(name, size=18):
    return U.ico(name, size)


import analytics  # noqa: E402,F401  (registers /admin, /admin/analytics/page, /admin/visitors/journey)
