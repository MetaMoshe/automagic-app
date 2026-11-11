"""Admin pages that sit in the left menu next to Clients and Consultants (portal.py):

  /admin/inbox      everything new: client messages, Book a Call requests, client drive uploads, portfolio sign-ups
  /admin            analytics (traffic, pages, referrers, places)
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


@bp.post("/admin/inbox/read")
def inbox_read():
    r = guard()
    if r:
        return r
    P.set_setting("inbox.seen", S.now())
    P.commit()
    return redirect("/admin/inbox")


# ---------------------------------------------------------------- analytics
@bp.get("/admin")
@bp.get("/admin/")
def analytics():
    r = guard()
    if r:
        return r
    days = 30
    since = (datetime.now(timezone.utc) - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    since7 = (datetime.now(timezone.utc) - timedelta(days=6)).strftime("%Y-%m-%d")
    visits = S.q("SELECT at, path, ref, vid, device, country, city FROM visits WHERE at >= ? ORDER BY at", (since,))
    v7 = [v for v in visits if v[0] >= since7]
    by_day = defaultdict(lambda: [0, set()])
    pages, refs, devices = defaultdict(int), defaultdict(int), defaultdict(int)
    countries, cities = defaultdict(set), defaultdict(set)
    for at, path, ref, vid, dev, country, city in visits:
        countries[country or "Unknown"].add(vid)
        if city:
            cities[f"{city}, {country}"].add(vid)
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
    top = lambda dct, n=10: sorted(dct.items(), key=lambda x: -x[1])[:n]
    return render_template_string(
        ANALYTICS, series=series, peak=peak, pages=top(pages), refs=top(refs), devices=top(devices, 3),
        countries=top({k: len(v) for k, v in countries.items()}), cities=top({k: len(v) for k, v in cities.items()}),
        total_visits=len(visits), v7=len(v7), u7=len({v[3] for v in v7}), days=days,
        tot_access=S.q("SELECT COUNT(*), COUNT(DISTINCT email) FROM access", one=True),
        tot_leads=S.q("SELECT COUNT(*) FROM leads", one=True)[0],
        leads7=S.q("SELECT COUNT(*) FROM leads WHERE at >= ?", (since7,), one=True)[0],
        access7=S.q("SELECT COUNT(DISTINCT email) FROM access WHERE at >= ?", (since7,), one=True)[0])


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
    return render_template_string(VISITORS, access=access, people=people, last_place=last_place,
                                  tot_access=S.q("SELECT COUNT(*), COUNT(DISTINCT email) FROM access", one=True))


# ---------------------------------------------------------------- templates
FOOT = U.FOOT

INBOX = U.head("inbox", "Inbox") + """
<div class="ph"><div><h2>Inbox</h2><p>Client messages, Book a Call requests, files your clients upload and portfolio sign-ups, newest first.</p></div>
{% if admin_nav.inbox %}<form method="post" action="/admin/inbox/read"><button class="ghost" type="submit">Mark all as read</button></form>{% endif %}</div>
<nav class="chips" aria-label="Filter"><a href="/admin/inbox" class="{{ 'on' if not kind }}">All</a>{% for k, label in types.items() %}<a href="/admin/inbox?type={{ k }}" class="{{ 'on' if kind == k }}">{{ label }}</a>{% endfor %}</nav>
{% if items %}<ul class="feed">{% for i in items %}<li class="{{ 'un' if i.unread }}"><a class="fi" href="{{ i.href }}"><span class="ico">{{ ico(i.icon)|safe }}</span><span class="tx"><b>{{ i.title }}</b><span>{{ i.sub }}</span></span><time datetime="{{ i.at }}">{{ i.at[:16]|replace('T',' ') }}</time></a></li>{% endfor %}</ul>
{% else %}<div class="card empty">Nothing here yet. New client messages, Book a Call requests, client uploads and portfolio sign-ups will appear here.</div>{% endif %}
<p class="mono" style="margin-top:14px;text-transform:none;letter-spacing:0">Times are UTC.</p>
""" + FOOT

ANALYTICS = U.head("chart", "Analytics") + """
<div class="ph"><div><h2>Analytics</h2><p>Visits to the website and the portfolio. Your own visits while signed in to admin aren't counted.</p></div></div>
<section><p class="mono">Last 7 days</p>
<div class="tiles">
<div class="tile"><span class="mono">Page views</span><b>{{ v7 }}</b><span>{{ total_visits }} in {{ days }} days</span></div>
<div class="tile"><span class="mono">Unique visitors</span><b>{{ u7 }}</b><span>by browser</span></div>
<a class="tile" href="/admin/visitors"><span class="mono">New access sign-ups</span><b>{{ access7 }}</b><span>{{ tot_access[1] }} unique emails in total</span></a>
<a class="tile" href="/admin/leads"><span class="mono">Book a Call leads</span><b>{{ leads7 }}</b><span>{{ tot_leads }} in total</span></a>
</div></section>
<section><h2 style="font-size:19px">Page views, last {{ days }} days <a href="/admin/export/visits.csv">Download CSV</a></h2>
<div class="card"><div class="chart" role="img" aria-label="Daily page views for the last {{ days }} days">
{% for d, n, u in series %}<div style="height:{{ (n / peak * 100)|round(1) }}%" title="{{ d }}: {{ n }} views, {{ u }} visitors"></div>{% endfor %}
</div><div class="axis"><span>{{ series[0][0] }}</span><span>Today</span></div></div></section>
<section><div class="grid3">
<div class="card"><p class="mono">Top pages</p>{% if pages %}<ul class="bars">{% for k, n in pages %}<li><span>{{ k }}</span><span>{{ n }}</span><i><b style="width:{{ (n / pages[0][1] * 100)|round }}%"></b></i></li>{% endfor %}</ul>{% else %}<p class="empty">No visits yet.</p>{% endif %}</div>
<div class="card"><p class="mono">Referrers</p>{% if refs %}<ul class="bars">{% for k, n in refs %}<li><span>{{ k }}</span><span>{{ n }}</span><i><b style="width:{{ (n / refs[0][1] * 100)|round }}%"></b></i></li>{% endfor %}</ul>{% else %}<p class="empty">No visits yet.</p>{% endif %}</div>
<div class="card"><p class="mono">Devices</p>{% if devices %}<ul class="bars">{% for k, n in devices %}<li><span>{{ k }}</span><span>{{ n }}</span><i><b style="width:{{ (n / devices[0][1] * 100)|round }}%"></b></i></li>{% endfor %}</ul>{% else %}<p class="empty">No visits yet.</p>{% endif %}</div>
</div></section>
<section><div class="grid2">
<div class="card"><p class="mono">Countries · visitors</p>{% if countries %}<ul class="bars">{% for k, n in countries %}<li><span>{{ k }}</span><span>{{ n }}</span><i><b style="width:{{ (n / countries[0][1] * 100)|round }}%"></b></i></li>{% endfor %}</ul>{% else %}<p class="empty">No visits yet.</p>{% endif %}</div>
<div class="card"><p class="mono">Cities · visitors</p>{% if cities %}<ul class="bars">{% for k, n in cities %}<li><span>{{ k }}</span><span>{{ n }}</span><i><b style="width:{{ (n / cities[0][1] * 100)|round }}%"></b></i></li>{% endfor %}</ul>{% else %}<p class="empty">No city data yet.</p>{% endif %}</div>
</div></section>
<p class="mono" style="margin:32px 0 0;text-transform:none;letter-spacing:0">Locations are approximate, from <a href="https://db-ip.com">IP Geolocation by DB-IP</a>.</p>
""" + FOOT

LEADS = U.head("leads", "Lead CRM") + """
<div class="ph"><div><h2>Lead CRM</h2><p>Book a Call requests from the website, plus leads you add yourself. Move each one through the pipeline.</p></div>
<div class="row"><a href="/admin/export/leads.csv">Download CSV</a><a class="btn" href="/admin/leads/new">Add lead</a></div></div>
{% if note %}<p class="ok" role="status">{{ note }}</p>{% endif %}
<nav class="chips" aria-label="Pipeline"><a href="/admin/leads" class="{{ 'on' if not status }}">All<b>{{ total }}</b></a>{% for s in statuses %}<a href="/admin/leads?status={{ s|urlencode }}" class="{{ 'on' if status == s }}">{{ s }}<b>{{ counts[s] }}</b></a>{% endfor %}</nav>
<form method="get" class="row" style="margin:0 0 14px;max-width:460px">{% if status %}<input type="hidden" name="status" value="{{ status }}">{% endif %}<input type="search" name="q" value="{{ qtext }}" placeholder="Search name, company or email" aria-label="Search leads"><button class="ghost" type="submit">Search</button></form>
{% if rows %}<div class="tw"><table><thead><tr><th>Received (UTC)</th><th>Name</th><th>Company</th><th>Email</th><th>Engagement</th><th>Status</th><th>Updated</th></tr></thead><tbody>
{% for id, at, name, company, email, eng, st, upd, cid in rows %}<tr class="rowlink" onclick="location='/admin/leads/{{ id }}'"><td class="nw">{{ at[:16]|replace('T',' ') }}</td><td><a href="/admin/leads/{{ id }}">{{ name }}</a></td><td>{{ company or '' }}</td><td>{{ email }}</td><td>{{ eng or '' }}</td>
<td><span class="pill{% if st == 'Won' %} won{% elif st == 'Lost' %} lost{% elif st != 'New' %} off{% endif %}">{{ st }}</span></td><td class="nw">{{ (upd or '')[:10] }}</td></tr>{% endfor %}
</tbody></table></div>{% else %}<div class="card empty">{% if status or qtext %}No leads match.{% else %}No leads yet. They appear here when someone sends the Book a Call form on the website.{% endif %}</div>{% endif %}
""" + FOOT

LEAD = U.head("leads", "Lead") + """
<section><p><a href="/admin/leads">← All leads</a></p><h2>{{ d.name or 'New lead' }}{% if lid %} <span class="pill{% if d.status == 'Won' %} won{% elif d.status == 'Lost' %} lost{% endif %}">{{ d.status }}</span>{% endif %}</h2>
{% if err %}<p class="err" role="alert">{{ err }}</p>{% elif note %}<p class="ok" role="status">{{ note }}</p>{% endif %}
<div class="split">
<form class="card" method="post" autocomplete="off">
 <p class="mono">Pipeline</p>
 <div class="grid">
  <div><label class="f" for="status">Status</label><select id="status" name="status">{% for s in statuses %}<option{% if d.status == s %} selected{% endif %}>{{ s }}</option>{% endfor %}</select></div>
  <div><label class="f" for="engagement">Engagement</label><select id="engagement" name="engagement"><option value="">Not set</option>{% for t in types %}<option{% if d.engagement == t %} selected{% endif %}>{{ t }}</option>{% endfor %}</select></div>
  <div class="full"><label class="f" for="admin_notes">Your notes (private)</label><textarea id="admin_notes" name="admin_notes" placeholder="Calls, next steps, budget, decision makers">{{ d.admin_notes or '' }}</textarea></div>
 </div>
 <p class="mono" style="margin-top:22px">Contact</p>
 <div class="grid">
  <div><label class="f" for="name">Full name</label><input type="text" id="name" name="name" value="{{ d.name or '' }}" required></div>
  <div><label class="f" for="email">Email</label><input type="email" id="email" name="email" value="{{ d.email or '' }}" required></div>
  <div><label class="f" for="company">Company</label><input type="text" id="company" name="company" value="{{ d.company or '' }}"></div>
  <div><label class="f" for="website">Website</label><input type="text" id="website" name="website" value="{{ d.website or '' }}"></div>
  <div class="full"><label class="f" for="notes">Their message</label><textarea id="notes" name="notes">{{ d.notes or '' }}</textarea></div>
 </div>
 <div class="row" style="margin-top:22px"><button type="submit">{% if lid %}Save changes{% else %}Add lead{% endif %}</button>{% if d.email %}<a class="btn ghost" href="mailto:{{ d.email }}">Email {{ d.name.split(' ')[0] }}</a>{% endif %}</div>
</form>
<div style="display:grid;gap:18px">
{% if lid %}
 <div class="card"><p class="mono">Details</p><dl class="kv">
  <dt>Received</dt><dd>{{ d.at[:16]|replace('T',' ') }} UTC</dd>
  <dt>From page</dt><dd>{{ d.page or 'Unknown' }}</dd>
  {% if d.access_email and d.access_email != d.email %}<dt>Portfolio email</dt><dd>{{ d.access_email }}</dd>{% endif %}
  {% if d.updated %}<dt>Last updated</dt><dd>{{ d.updated[:16]|replace('T',' ') }} UTC</dd>{% endif %}
 </dl></div>
 <div class="card"><p class="mono">Client</p>
  {% if client %}<p style="margin:6px 0 0">Converted to <a href="/admin/clients/{{ client.id }}">{{ client.company }}</a>.</p>
  {% else %}<p class="empty" style="font-size:14px;margin:4px 0 12px">{% if match %}A client with this email already exists: <a href="/admin/clients/{{ match.id }}">{{ match.company }}</a>. Converting links the lead to it.{% else %}Creates a client in the CRM from this lead and marks the lead Won. You can then turn on their portal login.{% endif %}</p>
  <form method="post" action="/admin/leads/{{ lid }}/convert"><button type="submit">Convert to client</button></form>{% endif %}
 </div>
 <form class="card" method="post" action="/admin/leads/{{ lid }}/delete"><p class="mono">Delete lead</p><p class="empty" style="font-size:14px;margin:4px 0 10px">This can't be undone. Type DELETE to confirm.</p>
  <div class="row"><input type="text" name="confirm" aria-label="Type DELETE to confirm" style="max-width:160px" autocomplete="off"><button class="danger" type="submit">Delete lead</button></div></form>
{% else %}<div class="card empty">Add someone you met outside the website, like a referral or an event contact.</div>{% endif %}
</div></div></section>
""" + FOOT

VISITORS = U.head("visitors", "Access and visitors") + """
<div class="ph"><div><h2>Access and visitors</h2><p>People who signed up with their email to view the portfolio, and what they looked at.</p></div></div>
<section id="access"><h2 style="font-size:19px">Access emails <span class="pill">{{ tot_access[1] }}</span> <a href="/admin/export/access.csv">Download CSV</a></h2>
{% if access %}<div class="tw"><table><thead><tr><th>Signed up (UTC)</th><th>Email</th><th>First page asked for</th><th>Location</th></tr></thead><tbody>
{% for at, email, page, country, city in access %}<tr><td class="nw">{{ at[:16]|replace('T',' ') }}</td><td><a href="mailto:{{ email }}">{{ email }}</a></td><td>{{ page }}</td><td>{{ [city, country]|select|join(', ') }}</td></tr>{% endfor %}
</tbody></table></div>{% else %}<div class="card empty">No sign-ups yet.</div>{% endif %}</section>
<section id="people"><h2 style="font-size:19px">Visitor activity by email</h2>
{% if people %}<div class="tw"><table><thead><tr><th>Email</th><th>Page views</th><th>Pages seen</th><th>Last seen (UTC)</th><th>Location</th></tr></thead><tbody>
{% for email, n, last, distinct in people %}<tr><td>{{ email }}</td><td>{{ n }}</td><td>{{ distinct }}</td><td class="nw">{{ last[:16]|replace('T',' ') }}</td><td>{{ last_place.get(email, '') }}</td></tr>{% endfor %}
</tbody></table></div>{% else %}<div class="card empty">No signed-in visits yet.</div>{% endif %}</section>
""" + FOOT


@bp.app_template_global("ico")
def _ico(name, size=18):
    return U.ico(name, size)
