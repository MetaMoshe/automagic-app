"""Client portal activity: logins, time spent in each section, clicks, downloads and uploads.

Rows go in `portal_events`:
  login     server side, when a client signs in (label = device)
  view      the client opened a portal section (view = section id); `secs` grows with active time
  click     a link or button they clicked (label = its text, target = where it goes)
  download  a file they opened (server side)
  action    uploads, messages, drive changes (label says what)
  page      a separate portal page they opened, e.g. How we work

`psid` groups one sign-in session. Nothing is recorded while an admin views the portal as the client.
Active time counts only while the tab is visible and the client was active within the last minute.
"""
import re
import secrets
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from flask import request, session

import server as S

VIEWS = {"home": "Overview", "inbox": "Inbox", "consultant": "Your consultant", "legal": "Project docs",
         "invoices": "Invoices", "drive": "Your Drive", "pricing": "Engagement & pricing", "roi": "ROI calculator",
         "settings": "Settings", "how-we-work": "How we work"}
KINDS = ("view", "time", "click", "action")


def init():
    pk = "SERIAL PRIMARY KEY" if S.DATABASE_URL else "INTEGER PRIMARY KEY AUTOINCREMENT"
    with S.app.app_context():
        S.q(f"CREATE TABLE IF NOT EXISTS portal_events (id {pk}, at TEXT NOT NULL, client_id INTEGER NOT NULL, psid TEXT, "
            "kind TEXT NOT NULL, view TEXT, label TEXT, target TEXT, secs INTEGER, eid TEXT)")
        S.q("CREATE INDEX IF NOT EXISTS portal_events_client ON portal_events (client_id, at)")
        S.q("CREATE INDEX IF NOT EXISTS portal_events_eid ON portal_events (eid)")
        if not S.DATABASE_URL:
            S.db().commit()


def _commit():
    if not S.DATABASE_URL:
        S.db().commit()


def viewing_as():
    return bool(session.get("view_as"))


def psid():
    if not session.get("psid"):
        session["psid"] = secrets.token_hex(8)
    return session["psid"]


def device():
    ua = request.user_agent.string or ""
    kind = "Phone" if re.search(r"Mobi|iPhone|Android", ua) else "Tablet" if re.search(r"iPad|Tablet", ua) else "Desktop"
    country, city = S.where()
    return " · ".join(x for x in (kind, ", ".join(y for y in (city, country) if y)) if x)


def log(cid, kind, view=None, label=None, target=None, secs=None, eid=None):
    if viewing_as() or not cid:
        return
    S.q("INSERT INTO portal_events (at, client_id, psid, kind, view, label, target, secs, eid) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (S.now(), cid, psid(), kind, view, S.clip(label, 120) or None, S.clip(target, 300) or None, secs, eid))
    _commit()


def login(cid):
    session["psid"] = secrets.token_hex(8)
    log(cid, "login", label=device())


def track(cid, d):
    """One event from the portal page (see TRACKER_JS)."""
    if viewing_as():
        return
    k = d.get("k")
    if k not in KINDS:
        return
    view = S.clip(d.get("v"), 30)
    view = view if view in VIEWS else None
    eid = S.clip(d.get("eid"), 40) or None
    if k == "time":
        try:
            secs = max(0, min(int(d.get("secs") or 0), 8 * 3600))
        except (TypeError, ValueError):
            return
        if eid:
            S.q("UPDATE portal_events SET secs = ? WHERE eid = ? AND client_id = ? AND kind = 'view'", (secs, eid, cid))
            _commit()
        return
    if k == "view" and not view:
        return
    log(cid, k, view, d.get("l"), d.get("t"), 0 if k == "view" else None, eid if k == "view" else None)


def since(days):
    return (datetime.now(timezone.utc) - timedelta(days=days - 1)).strftime("%Y-%m-%d")


COLS = "at, client_id, psid, kind, view, label, target, secs"


def events(cid=None, days=None):
    sql, args = f"SELECT {COLS} FROM portal_events WHERE 1=1", []
    if cid:
        sql += " AND client_id = ?"
        args.append(cid)
    if days:
        sql += " AND at >= ?"
        args.append(since(days))
    return [dict(zip(COLS.replace(" ", "").split(","), r)) for r in S.q(sql + " ORDER BY at, id", args)]


def summary(evs):
    """Totals for a list of events (one client)."""
    logins = [e for e in evs if e["kind"] == "login"]
    by_view, clicks = defaultdict(int), defaultdict(int)
    for e in evs:
        if e["kind"] == "view":
            by_view[VIEWS.get(e["view"], e["view"])] += e["secs"] or 0
        elif e["kind"] in ("click", "download", "action"):
            clicks[e["label"] or e["target"] or "?"] += 1
    return {"logins": len(logins), "last_login": logins[-1]["at"] if logins else None,
            "time": sum(by_view.values()), "by_view": sorted(by_view.items(), key=lambda x: -x[1]),
            "clicks": sorted(clicks.items(), key=lambda x: -x[1]),
            "n_clicks": sum(1 for e in evs if e["kind"] == "click"),
            "downloads": sum(1 for e in evs if e["kind"] == "download"),
            "sessions": len({e["psid"] for e in evs if e["psid"]})}


def sessions(evs):
    """Sign-in sessions, newest first, each with its steps in order."""
    groups, order = defaultdict(list), []
    for e in evs:
        key = e["psid"] or e["at"][:13]
        if key not in groups:
            order.append(key)
        groups[key].append(e)
    out = []
    for key in reversed(order):
        es = groups[key]
        login = next((e for e in es if e["kind"] == "login"), None)
        out.append({"start": es[0]["at"], "end": es[-1]["at"], "device": login["label"] if login else "",
                    "time": sum(e["secs"] or 0 for e in es if e["kind"] == "view"),
                    "steps": [e for e in es if e["kind"] != "login"]})
    return out


TRACKER_JS = r"""
(function(){
  var cur=null,eid=null,secs=0,last=Date.now(),dirty=false;
  function rid(){return Math.random().toString(36).slice(2)+Date.now().toString(36)}
  function send(d){d=JSON.stringify(d);try{if(navigator.sendBeacon&&navigator.sendBeacon('/portal/api/track',new Blob([d],{type:'application/json'})))return}catch(e){}
    try{fetch('/portal/api/track',{method:'POST',headers:{'Content-Type':'application/json'},body:d,keepalive:true,credentials:'same-origin'}).catch(function(){})}catch(e){}}
  function flush(){if(eid&&dirty){dirty=false;send({k:'time',eid:eid,secs:secs})}}
  function open(v){if(v===cur)return;flush();cur=v;eid=rid();secs=0;send({k:'view',v:v,eid:eid})}
  function view(){var v=(location.hash||'#home').slice(1);return window.AM_VIEWS.indexOf(v)<0?'home':v}
  ['mousemove','keydown','scroll','touchstart','click','wheel'].forEach(function(e){addEventListener(e,function(){last=Date.now()},{passive:true,capture:true})});
  setInterval(function(){if(document.visibilityState!=='visible'||Date.now()-last>6e4)return;secs++;dirty=true;if(secs%15===0)flush()},1000);
  addEventListener('hashchange',function(){open(view())});
  document.addEventListener('visibilitychange',function(){if(document.visibilityState==='hidden')flush()});
  addEventListener('pagehide',flush);
  document.addEventListener('click',function(e){var el=e.target.closest&&e.target.closest('a,button,[role=button],summary');if(!el||el.closest('nav.side')||el.closest('#drive-root'))return;
    var href=el.getAttribute('href')||'';if(/^\/portal\/files\//.test(href))return;
    var l=(el.getAttribute('aria-label')||el.textContent||'').replace(/\s+/g,' ').trim().slice(0,80);if(!l&&!href)return;
    send({k:'click',v:cur,l:l,t:href.slice(0,300)})},true);
  window.amTrack=function(k,l){send({k:k==='action'?'action':'click',v:cur,l:l})};
  open(view());
})();
"""
