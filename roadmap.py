"""Project roadmap: sprints and tasks per client, managed in the CRM (Roadmap tab) and shown in the portal
(Project roadmap) as a list, columns (board) or a timeline.

Tables: roadmap_sprints (name, start, end; no dates = unscheduled, e.g. "Later") and roadmap_items.
CSV import and export use the same columns as the sprint-timeline template:
Task, ID, Sprint, Sprint start, Sprint end, Product, Lane, Priority, Days (est.), Owner, Status, Done on,
Depends on, Feature, Rank after 1 Jan (provisional), Notes. Items marked hidden stay internal to the admin.
"""
import csv
import io
import json
import re
from datetime import date

import server as S

FIELDS = ["ref", "title", "product", "lane", "priority", "days", "owner", "status", "done_on", "depends", "feature", "rank", "notes"]
STATUSES = ["Not started", "In progress", "In review", "Blocked", "Done", "Parked"]
PRIORITIES = ["Critical", "Urgent", "Important", "Normal", "Later", "Done"]
CSV_COLS = [("Task", "title"), ("ID", "ref"), ("Sprint", None), ("Sprint start", None), ("Sprint end", None), ("Product", "product"),
            ("Lane", "lane"), ("Priority", "priority"), ("Days (est.)", "days"), ("Owner", "owner"), ("Status", "status"),
            ("Done on", "done_on"), ("Depends on", "depends"), ("Feature", "feature"), ("Rank after 1 Jan (provisional)", "rank"), ("Notes", "notes")]


def init():
    pk = "SERIAL PRIMARY KEY" if S.DATABASE_URL else "INTEGER PRIMARY KEY AUTOINCREMENT"
    with S.app.app_context():
        S.q(f"CREATE TABLE IF NOT EXISTS roadmap_sprints (id {pk}, client_id INTEGER NOT NULL, name TEXT NOT NULL, "
            "start_on TEXT, end_on TEXT, position INTEGER DEFAULT 0)")
        S.q(f"CREATE TABLE IF NOT EXISTS roadmap_items (id {pk}, client_id INTEGER NOT NULL, sprint_id INTEGER, "
            + ", ".join(f"{f} TEXT" for f in FIELDS) + ", hidden INTEGER DEFAULT 0, position INTEGER DEFAULT 0, updated TEXT)")
        S.q("CREATE INDEX IF NOT EXISTS roadmap_items_client ON roadmap_items (client_id)")
        S.q("CREATE INDEX IF NOT EXISTS roadmap_sprints_client ON roadmap_sprints (client_id)")
        if not S.DATABASE_URL:
            S.db().commit()


def _commit():
    if not S.DATABASE_URL:
        S.db().commit()


def _date(v):
    v = str(v or "").strip()[:10]
    return v if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) else None


def sprints(cid):
    rows = [{"id": r[0], "name": r[1], "start": r[2], "end": r[3], "position": r[4] or 0}
            for r in S.q("SELECT id, name, start_on, end_on, position FROM roadmap_sprints WHERE client_id = ?", (cid,))]
    rows.sort(key=lambda s: (s["start"] is None, s["start"] or "", s["position"], s["id"]))
    return rows


ITEM_COLS = ["id", "sprint_id"] + FIELDS + ["hidden", "position", "updated"]


def items(cid, visible_only=False):
    sql = f"SELECT {', '.join(ITEM_COLS)} FROM roadmap_items WHERE client_id = ?" + (" AND COALESCE(hidden, 0) = 0" if visible_only else "")
    rows = [dict(zip(ITEM_COLS, r)) for r in S.q(sql + " ORDER BY position, id", (cid,))]
    return rows


def item(cid, iid):
    r = S.q(f"SELECT {', '.join(ITEM_COLS)} FROM roadmap_items WHERE id = ? AND client_id = ?", (iid, cid), one=True)
    return dict(zip(ITEM_COLS, r)) if r else None


def save_item(cid, iid, f):
    vals = {k: S.clip(f.get(k), 2000 if k in ("notes", "title") else 200) for k in FIELDS}
    vals["done_on"] = _date(vals["done_on"]) or ""
    sid = int(f["sprint_id"]) if str(f.get("sprint_id") or "").isdigit() else None
    if sid and not S.q("SELECT id FROM roadmap_sprints WHERE id = ? AND client_id = ?", (sid, cid), one=True):
        sid = None
    if vals["status"] == "Done" and not vals["done_on"]:
        vals["done_on"] = date.today().isoformat()
    hidden = 1 if f.get("hidden") else 0
    if iid:
        S.q(f"UPDATE roadmap_items SET {', '.join(k + ' = ?' for k in FIELDS)}, sprint_id = ?, hidden = ?, updated = ? WHERE id = ? AND client_id = ?",
            (*[vals[k] for k in FIELDS], sid, hidden, S.now(), iid, cid))
    else:
        pos = (S.q("SELECT MAX(position) FROM roadmap_items WHERE client_id = ?", (cid,), one=True)[0] or 0) + 1
        if not vals["ref"]:
            n = S.q("SELECT COUNT(*) FROM roadmap_items WHERE client_id = ?", (cid,), one=True)[0] + 1
            vals["ref"] = f"T-{n:03d}"
        S.q(f"INSERT INTO roadmap_items (client_id, sprint_id, {', '.join(FIELDS)}, hidden, position, updated) VALUES (?, ?, {', '.join('?' * len(FIELDS))}, ?, ?, ?)",
            (cid, sid, *[vals[k] for k in FIELDS], hidden, pos, S.now()))
        iid = S.q("SELECT MAX(id) FROM roadmap_items WHERE client_id = ?", (cid,), one=True)[0]
    _commit()
    return iid


def set_status(cid, iid, status):
    status = S.clip(status, 60)
    done = date.today().isoformat() if status == "Done" else None
    if status == "Done":
        S.q("UPDATE roadmap_items SET status = ?, done_on = COALESCE(NULLIF(done_on, ''), ?), updated = ? WHERE id = ? AND client_id = ?",
            (status, done, S.now(), iid, cid))
    else:
        S.q("UPDATE roadmap_items SET status = ?, updated = ? WHERE id = ? AND client_id = ?", (status, S.now(), iid, cid))
    _commit()


def save_sprint(cid, spid, name, start, end):
    name = S.clip(name, 120)
    if not name:
        return "Give the sprint a name."
    start, end = _date(start), _date(end)
    if start and end and end < start:
        return "The sprint ends before it starts."
    if spid:
        S.q("UPDATE roadmap_sprints SET name = ?, start_on = ?, end_on = ? WHERE id = ? AND client_id = ?", (name, start, end, spid, cid))
    else:
        S.q("INSERT INTO roadmap_sprints (client_id, name, start_on, end_on, position) VALUES (?, ?, ?, ?, ?)",
            (cid, name, start, end, len(sprints(cid))))
    _commit()
    return None


def delete_sprint(cid, spid):
    S.q("UPDATE roadmap_items SET sprint_id = NULL WHERE sprint_id = ? AND client_id = ?", (spid, cid))
    S.q("DELETE FROM roadmap_sprints WHERE id = ? AND client_id = ?", (spid, cid))
    _commit()


# ---------------------------------------------------------------- CSV
def _norm(h):
    return re.sub(r"[^a-z0-9]", "", (h or "").lower())


def import_csv(cid, data, replace):
    """Returns (added, updated, error)."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return 0, 0, "The file is empty."
    head = [_norm(h) for h in rows[0]]
    want = {_norm(c): k for c, k in CSV_COLS if k}
    alias = {"task": "title", "title": "title", "name": "title", "id": "ref", "estimate": "days", "days": "days", "daysest": "days",
             "rank": "rank", "dependson": "depends", "depends": "depends", "doneon": "done_on", "done": "done_on"}
    col = {}
    for i, h in enumerate(head):
        key = want.get(h) or alias.get(h) or ("rank" if h.startswith("rank") else None)
        if key and key not in col:
            col[key] = i
    sp_i = {k: head.index(_norm(k)) if _norm(k) in head else None for k in ("Sprint", "Sprint start", "Sprint end")}
    if "title" not in col:
        return 0, 0, "Couldn't find a Task column. Use the template's column names."
    if replace:
        S.q("DELETE FROM roadmap_items WHERE client_id = ?", (cid,))
        S.q("DELETE FROM roadmap_sprints WHERE client_id = ?", (cid,))
    by_name = {s["name"].lower(): s["id"] for s in sprints(cid)}
    existing = {} if replace else {(r["ref"] or "").lower(): r["id"] for r in items(cid) if r["ref"]}
    added = updated = 0
    pos = (S.q("SELECT MAX(position) FROM roadmap_items WHERE client_id = ?", (cid,), one=True)[0] or 0)
    for row in rows[1:5001]:
        get = lambda i: (row[i].strip() if i is not None and i < len(row) else "")
        title = get(col["title"])
        if not title:
            continue
        sname = get(sp_i["Sprint"])
        sid = None
        if sname:
            sid = by_name.get(sname.lower())
            if not sid:
                S.q("INSERT INTO roadmap_sprints (client_id, name, start_on, end_on, position) VALUES (?, ?, ?, ?, ?)",
                    (cid, sname[:120], _date(get(sp_i["Sprint start"])), _date(get(sp_i["Sprint end"])), len(by_name)))
                sid = by_name[sname.lower()] = S.q("SELECT MAX(id) FROM roadmap_sprints WHERE client_id = ?", (cid,), one=True)[0]
        vals = {k: S.clip(get(col.get(k)), 2000 if k in ("notes", "title") else 200) for k in FIELDS}
        vals["title"] = title[:2000]
        vals["done_on"] = _date(vals["done_on"]) or ""
        ref = vals["ref"].lower()
        if ref and ref in existing:
            S.q(f"UPDATE roadmap_items SET {', '.join(k + ' = ?' for k in FIELDS)}, sprint_id = ?, updated = ? WHERE id = ?",
                (*[vals[k] for k in FIELDS], sid, S.now(), existing[ref]))
            updated += 1
        else:
            pos += 1
            S.q(f"INSERT INTO roadmap_items (client_id, sprint_id, {', '.join(FIELDS)}, hidden, position, updated) VALUES (?, ?, {', '.join('?' * len(FIELDS))}, 0, ?, ?)",
                (cid, sid, *[vals[k] for k in FIELDS], pos, S.now()))
            added += 1
    _commit()
    return added, updated, None


def export_csv(cid):
    sp = {s["id"]: s for s in sprints(cid)}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([c for c, _ in CSV_COLS] + ["Hidden from client"])
    for it in items(cid):
        s = sp.get(it["sprint_id"]) or {}
        row = []
        for c, k in CSV_COLS:
            v = {"Sprint": s.get("name"), "Sprint start": s.get("start"), "Sprint end": s.get("end")}.get(c) if k is None else it.get(k)
            v = "" if v is None else str(v)
            row.append("'" + v if v[:1] in "=+-@" else v)
        w.writerow(row + ["yes" if it["hidden"] else ""])
    return buf.getvalue()


# ---------------------------------------------------------------- views
def data(cid, visible_only):
    sp = sprints(cid)
    its = items(cid, visible_only)
    used = {i["sprint_id"] for i in its}
    out_sp = [s for s in sp if s["id"] in used or not visible_only]
    return {"sprints": out_sp, "items": [{k: it[k] for k in ["id", "sprint_id"] + FIELDS + (["hidden"] if not visible_only else [])} for it in its],
            "today": date.today().isoformat()}


def data_json(cid, visible_only):
    return json.dumps(data(cid, visible_only)).replace("</", "<\\/")


# The client-facing roadmap: list, columns and timeline, all rendered from data() in the browser.
CSS = """
.rm-bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:0 0 16px}
.rm-seg{display:inline-flex;background:var(--bg-2,var(--bg));border:1px solid var(--line);border-radius:10px;padding:3px}
.rm-seg button{background:transparent;border:0;color:var(--muted);font:500 14px var(--fb,Inter,sans-serif);padding:7px 14px;border-radius:7px;cursor:pointer;display:inline-flex;align-items:center;gap:7px}
.rm-seg button.on{background:var(--surface-2,var(--surface));color:var(--ink);box-shadow:inset 0 0 0 1px var(--line-2,var(--line))}
.rm-bar select,.rm-bar input{font:14px var(--fb,Inter,sans-serif);color:var(--ink);background:var(--bg-2,var(--bg));border:1px solid var(--line);border-radius:9px;padding:8px 10px;max-width:220px}
.rm-bar input{flex:1;min-width:150px}
.rm-sum{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:0 0 18px}
@media (max-width:760px){.rm-sum{grid-template-columns:repeat(2,minmax(0,1fr))}}
.rm-sum>div{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.rm-sum b{display:block;font:600 24px var(--fd,"Space Grotesk",sans-serif);margin:4px 0 2px}.rm-sum span{color:var(--muted);font-size:12.5px}
.rm-sum small{display:block;font:500 11px var(--fm,monospace);letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.rm-prog{height:6px;border-radius:4px;background:var(--line);overflow:hidden;margin-top:8px}.rm-prog i{display:block;height:100%;background:var(--ok,#7FD3A6)}
.rm-grp{margin:0 0 18px;border:1px solid var(--line);border-radius:12px;background:var(--surface);overflow:hidden}
.rm-grp>header{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:12px 16px;background:var(--surface-2,rgba(127,207,224,.06));border-bottom:1px solid var(--line)}
.rm-grp>header h3{font-size:16px;margin:0}.rm-grp>header span{color:var(--muted);font-size:13px}
.rm-grp>header .rm-prog{flex:0 0 120px;margin:0 0 0 auto}
.rm-row{display:grid;grid-template-columns:14px minmax(0,1fr) auto;gap:12px;align-items:start;padding:11px 16px;border-top:1px solid var(--line)}
.rm-row:first-of-type{border-top:0}
.rm-dot{width:10px;height:10px;border-radius:50%;margin-top:6px;background:var(--faint,#6E7D90)}
.rm-t{min-width:0}.rm-t b{font-weight:500;display:block;line-height:1.4}.rm-t small{display:block;color:var(--muted);font-size:12.5px;margin-top:3px}
.rm-tags select.rm-pill{background:transparent;font:500 11px var(--fm,monospace);cursor:pointer;padding:3px 6px}
.rm-tags{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end;align-items:center}
.rm-pill{font:500 11px var(--fm,monospace);letter-spacing:.03em;padding:3px 8px;border-radius:999px;border:1px solid var(--line-2,var(--line));color:var(--muted);white-space:nowrap}
.st-done{--c:#7FD3A6}.st-in-progress,.st-in-review{--c:#7FD3E4}.st-blocked{--c:#F2B8B5}.st-not-started{--c:#9BA9BA}.st-parked{--c:#6E7D90}
.rm-dot[class*=st-]{background:var(--c)}.rm-pill[class*=st-]{color:var(--c);border-color:color-mix(in srgb,var(--c) 45%,transparent)}
.pr-critical{color:#F2B8B5;border-color:#6b3434}.pr-urgent{color:#F0B37E;border-color:#6b4a2a}.pr-important{color:#F0C987;border-color:#5f5130}
.rm-board{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(250px,1fr);gap:12px;overflow-x:auto;padding-bottom:8px}
.rm-col{background:var(--bg-2,var(--bg));border:1px solid var(--line);border-radius:12px;padding:10px;min-width:0;align-self:start}
.rm-col>header{display:flex;align-items:center;gap:8px;padding:4px 4px 10px;font-weight:600;font-size:14px}.rm-col>header span{margin-left:auto;color:var(--muted);font:500 12px var(--fm,monospace)}
.rm-card{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin-top:8px}
.rm-card b{font-weight:500;font-size:14px;line-height:1.4;display:block}.rm-card small{display:block;color:var(--muted);font-size:12px;margin:4px 0 6px}
.rm-card .rm-tags{justify-content:flex-start}
.rm-more{width:100%;margin-top:8px;background:transparent;border:1px dashed var(--line-2,var(--line));color:var(--muted);border-radius:8px;padding:7px;cursor:pointer;font:inherit;font-size:13px}
.rm-tl{border:1px solid var(--line);border-radius:12px;background:var(--surface);overflow-x:auto}
.rm-tl-in{min-width:760px;position:relative}
.rm-axis,.rm-tr{display:grid;grid-template-columns:260px minmax(0,1fr)}
.rm-axis{position:sticky;top:0;background:var(--surface-2,var(--surface));border-bottom:1px solid var(--line);z-index:2}
.rm-axis>div:first-child{padding:10px 16px;font:500 11px var(--fm,monospace);letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.rm-ticks{position:relative;height:36px}.rm-ticks span{position:absolute;top:10px;font:500 11px var(--fm,monospace);color:var(--muted);transform:translateX(-50%);white-space:nowrap}
.rm-ticks span::before{content:"";position:absolute;left:50%;top:-10px;height:6px;border-left:1px solid var(--line-2,var(--line))}
.rm-tr{border-top:1px solid var(--line);min-height:34px}.rm-tr.sp{background:rgba(127,207,224,.05)}
.rm-tr>div:first-child{padding:8px 16px;font-size:13.5px;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rm-tr.sp>div:first-child{font-weight:600;font-size:14px;white-space:normal}
.rm-tr.sp>div:first-child button{all:unset;cursor:pointer;display:flex;gap:8px;align-items:center}
.rm-lane{position:relative}
.rm-gb{position:absolute;top:8px;bottom:8px;border-radius:6px;background:color-mix(in srgb,var(--c,#9BA9BA) 22%,transparent);border:1px solid color-mix(in srgb,var(--c,#9BA9BA) 55%,transparent);overflow:hidden}
.rm-gb i{position:absolute;inset:0 auto 0 0;background:color-mix(in srgb,#7FD3A6 45%,transparent)}
.rm-gb em{position:relative;font:600 11px var(--fm,monospace);padding:0 8px;line-height:16px;color:var(--ink);white-space:nowrap;font-style:normal;z-index:2;text-shadow:0 0 3px var(--bg,#0F151E)}
.rm-tr.task .rm-gb{top:11px;bottom:11px}
.rm-ms{position:absolute;top:50%;width:11px;height:11px;margin:-6px 0 0 -6px;transform:rotate(45deg);background:#7FD3A6;border-radius:2px}
.rm-today{position:absolute;top:0;bottom:0;border-left:2px solid #F0C987;z-index:1;pointer-events:none}
.rm-today span{position:absolute;top:2px;left:4px;font:600 10px var(--fm,monospace);color:#F0C987;background:var(--surface);padding:0 3px}
.rm-later{padding:12px 16px;border-top:1px solid var(--line);color:var(--muted);font-size:13.5px}
.rm-empty{padding:28px;border:1px dashed var(--line-2,var(--line));border-radius:12px;color:var(--muted);text-align:center}
@media (max-width:640px){.rm-row{grid-template-columns:14px minmax(0,1fr)}.rm-row .rm-tags{grid-column:2;justify-content:flex-start}}
"""

JS = r"""
function amRoadmap(root,D,opts){
  opts=opts||{};var key='am-rm-'+(opts.key||'');
  var st={view:'list',product:'',status:'',q:'',group:'status',open:{}};
  try{var s=JSON.parse(localStorage.getItem(key)||'null');if(s){st.view=s.view||st.view;st.group=s.group||st.group}}catch(e){}
  function save(){try{localStorage.setItem(key,JSON.stringify({view:st.view,group:st.group}))}catch(e){}}
  function E(t,a,k){var e=document.createElement(t);for(var x in a||{}){if(x==='text')e.textContent=a[x];else if(x.slice(0,2)==='on')e.addEventListener(x.slice(2),a[x]);else e.setAttribute(x,a[x])}(k||[]).forEach(function(c){if(c!=null&&c!==false)e.appendChild(typeof c==='string'?document.createTextNode(c):c)});return e}
  function slug(s){return (s||'').toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'')}
  var SP={};D.sprints.forEach(function(s){SP[s.id]=s});
  var ORDER=['Not started','In progress','In review','Blocked','Done','Parked'];
  function isDone(i){return (i.status||'').toLowerCase()==='done'}
  function fmt(d){if(!d)return'';var p=d.split('-');return +p[2]+' '+['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][+p[1]-1]}
  function range(s){return s&&s.start?fmt(s.start)+(s.end?' – '+fmt(s.end):''):'Not scheduled'}
  function filtered(){var q=st.q.toLowerCase();return D.items.filter(function(i){return (!st.product||i.product===st.product)&&(!st.status||(i.status||'')===st.status)&&(!q||((i.title||'')+' '+(i.ref||'')+' '+(i.feature||'')+' '+(i.lane||'')).toLowerCase().indexOf(q)>=0)})}
  function groups(list){var g=[],by={};D.sprints.forEach(function(s){by[s.id]={s:s,items:[]};g.push(by[s.id])});var none={s:{id:null,name:'No sprint'},items:[]};
    list.forEach(function(i){(by[i.sprint_id]||none).items.push(i)});if(none.items.length)g.push(none);return g.filter(function(x){return x.items.length})}
  function prog(items){var d=items.filter(isDone).length;return E('div',{'class':'rm-prog',title:d+' of '+items.length+' done'},[E('i',{style:'width:'+(items.length?Math.round(d/items.length*100):0)+'%'})])}
  function tags(i){return E('span',{'class':'rm-tags'},[i.priority&&i.priority!=='Done'?E('span',{'class':'rm-pill pr-'+slug(i.priority),text:i.priority}):null,opts.onStatus?statusSel(i):i.status?E('span',{'class':'rm-pill st-'+slug(i.status),text:i.status}):null,
    i.days&&i.days!=='0'?E('span',{'class':'rm-pill',text:i.days+(i.days==='1'?' day':' days')}):null,opts.admin&&+i.hidden?E('span',{'class':'rm-pill',text:'Hidden'}):null])}
  function statusSel(i){var o=ORDER.slice();if(i.status&&o.indexOf(i.status)<0)o.push(i.status);
    var s=E('select',{'class':'rm-pill st-'+slug(i.status),'aria-label':'Status of '+i.title},o.map(function(v){return E('option',{value:v,text:v})}));s.value=i.status||'Not started';
    s.addEventListener('change',function(){var v=s.value;opts.onStatus(i.id,v).then(function(ok){if(ok){i.status=v;if(v==='Done'&&!i.done_on)i.done_on=D.today;draw()}else{s.value=i.status}})});return s}
  function meta(i){return [i.ref,i.product,i.lane,i.feature,isDone(i)&&i.done_on?'Done '+fmt(i.done_on):'',i.depends?'Depends on: '+i.depends:''].filter(Boolean).join(' · ')}
  function title(i){return opts.admin?E('a',{href:opts.admin+i.id,text:i.title}):E('span',{text:i.title})}
  function list(items){var w=E('div');groups(items).forEach(function(g){var d=g.items.filter(isDone).length;
    w.appendChild(E('section',{'class':'rm-grp'},[E('header',{},[E('h3',{text:g.s.name}),E('span',{text:range(g.s)+' · '+d+'/'+g.items.length+' done'}),prog(g.items)])].concat(
      g.items.map(function(i){return E('div',{'class':'rm-row'},[E('span',{'class':'rm-dot st-'+slug(i.status)}),E('div',{'class':'rm-t'},[E('b',{},[title(i)]),E('small',{text:meta(i)}),i.notes?E('small',{text:i.notes}):null]),tags(i)])}))))});return w}
  function board(items){var cols=[],by={};
    if(st.group==='sprint'){groups(items).forEach(function(g){cols.push({name:g.s.name,sub:range(g.s),items:g.items})})}
    else{items.forEach(function(i){var k=i.status||'No status';if(!by[k]){by[k]={name:k,items:[]};cols.push(by[k])}by[k].items.push(i)});
      cols.sort(function(a,b){var x=ORDER.indexOf(a.name),y=ORDER.indexOf(b.name);return (x<0?99:x)-(y<0?99:y)})}
    var w=E('div',{'class':'rm-board'});cols.forEach(function(c){var lim=st.open[c.name]?1e9:25,col=E('div',{'class':'rm-col'},[E('header',{},[st.group==='status'?E('span',{'class':'rm-dot st-'+slug(c.name),style:'margin:0'}):null,E('b',{text:c.name}),E('span',{text:String(c.items.length)})])]);
      c.items.slice(0,lim).forEach(function(i){var sp=SP[i.sprint_id];col.appendChild(E('div',{'class':'rm-card'},[E('b',{},[title(i)]),E('small',{text:[i.ref,i.product,st.group==='status'&&sp?sp.name:i.status].filter(Boolean).join(' · ')}),tags(i)]))});
      if(c.items.length>lim)col.appendChild(E('button',{'class':'rm-more',type:'button',text:'Show '+(c.items.length-lim)+' more',onclick:function(){st.open[c.name]=1;draw()}}));w.appendChild(col)});return w}
  function timeline(items){var gs=groups(items),dated=gs.filter(function(g){return g.s.start}),later=gs.filter(function(g){return !g.s.start});
    var w=E('div',{'class':'rm-tl'}),inn=E('div',{'class':'rm-tl-in'});w.appendChild(inn);
    if(!dated.length){inn.appendChild(E('div',{'class':'rm-later',text:'No sprints have dates yet.'}))}
    else{var t0=new Date(dated[0].s.start),t1=new Date(dated[0].s.end||dated[0].s.start);dated.forEach(function(g){var a=new Date(g.s.start),b=new Date(g.s.end||g.s.start);if(a<t0)t0=a;if(b>t1)t1=b});
      var span=(t1-t0)/864e5+1;function x(d){return Math.max(0,Math.min(100,((new Date(d)-t0)/864e5)/span*100))}
      var ticks=E('div',{'class':'rm-ticks'});for(var d=new Date(t0);d<=t1;d.setDate(d.getDate()+7)){var iso=d.toISOString().slice(0,10);ticks.appendChild(E('span',{style:'left:'+x(iso)+'%',text:fmt(iso)}))}
      inn.appendChild(E('div',{'class':'rm-axis'},[E('div',{text:'Sprint / task'}),ticks]));
      dated.forEach(function(g){var s=g.s,dn=g.items.filter(isDone).length,pc=Math.round(dn/g.items.length*100),open=st.open['t'+s.id]!==undefined?st.open['t'+s.id]:(D.today<=(s.end||s.start));
        var bar=E('div',{'class':'rm-gb',style:'left:'+x(s.start)+'%;width:'+Math.max(1.5,x(s.end||s.start)+100/span-x(s.start))+'%;--c:#7FD3E4'},[E('i',{style:'width:'+pc+'%'}),E('em',{text:pc+'% done'})]);
        inn.appendChild(E('div',{'class':'rm-tr sp'},[E('div',{},[E('button',{type:'button','aria-expanded':String(open),onclick:function(){st.open['t'+s.id]=!open;draw()}},[(open?'▾ ':'▸ ')+s.name,E('small',{style:'font-weight:400;color:var(--muted)',text:' '+g.items.length})])]),E('div',{'class':'rm-lane'},[bar])]));
        if(open)g.items.forEach(function(i){var lane=E('div',{'class':'rm-lane'});
          lane.appendChild(E('div',{'class':'rm-gb st-'+slug(i.status),title:i.title+' · '+(i.status||''),style:'left:'+x(s.start)+'%;width:'+Math.max(1.5,x(s.end||s.start)+100/span-x(s.start))+'%'}));
          if(isDone(i)&&i.done_on)lane.appendChild(E('span',{'class':'rm-ms',title:'Done '+fmt(i.done_on),style:'left:'+x(i.done_on)+'%'}));
          inn.appendChild(E('div',{'class':'rm-tr task'},[E('div',{title:i.title},[E('span',{'class':'rm-dot st-'+slug(i.status),style:'display:inline-block;margin:0 8px 0 0'}),i.title]),lane]))})});
      if(D.today>=dated[0].s.start&&D.today<=t1.toISOString().slice(0,10)){var tl=E('div',{'class':'rm-today',style:'left:calc(260px + (100% - 260px) * '+(x(D.today)/100)+')'},[E('span',{text:'Today'})]);inn.appendChild(tl)}}
    later.forEach(function(g){inn.appendChild(E('div',{'class':'rm-later'},[E('b',{style:'color:var(--ink)',text:g.s.name+' · '+g.items.length+' tasks'}),' not scheduled yet. See them in the list or columns view.']))});
    return w}
  function draw(){root.innerHTML='';var all=D.items,done=all.filter(isDone).length,today=D.today,cur=null;
    D.sprints.forEach(function(s){if(!cur&&s.start&&s.end&&s.start<=today&&today<=s.end)cur=s});if(!cur)D.sprints.forEach(function(s){if(!cur&&s.start&&s.start>today)cur=s});
    var ci=cur?all.filter(function(i){return i.sprint_id===cur.id}):[],cd=ci.filter(isDone).length,next=all.filter(function(i){return !isDone(i)&&cur&&i.sprint_id===cur.id}).length;
    var sched=all.filter(function(i){return SP[i.sprint_id]&&SP[i.sprint_id].start}),sd=sched.filter(isDone).length;
    root.appendChild(E('div',{'class':'rm-sum'},[
      E('div',{},[E('small',{text:'Overall'}),E('b',{text:(sched.length?Math.round(sd/sched.length*100):0)+'%'}),E('span',{text:sd+' of '+sched.length+' scheduled tasks done'}),E('div',{'class':'rm-prog'},[E('i',{style:'width:'+(sched.length?sd/sched.length*100:0)+'%'})])]),
      E('div',{},[E('small',{text:cur&&cur.start<=today?'Current sprint':'Next sprint'}),E('b',{style:'font-size:17px;line-height:1.3',text:cur?cur.name:'–'}),E('span',{text:cur?range(cur):''})]),
      E('div',{},[E('small',{text:'Sprint progress'}),E('b',{text:cur?cd+'/'+ci.length:'–'}),E('span',{text:cur?next+' still to do':''}),cur?prog(ci):null]),
      E('div',{},[E('small',{text:'Completed'}),E('b',{text:String(done)}),E('span',{text:'tasks done in total'})])]));
    var products=[];all.forEach(function(i){if(i.product&&products.indexOf(i.product)<0)products.push(i.product)});products.sort();
    var statuses=[];all.forEach(function(i){if(i.status&&statuses.indexOf(i.status)<0)statuses.push(i.status)});statuses.sort(function(a,b){var x=ORDER.indexOf(a),y=ORDER.indexOf(b);return (x<0?99:x)-(y<0?99:y)});
    function sel(lbl,arr,k){var s=E('select',{'aria-label':lbl},[E('option',{value:'',text:lbl})].concat(arr.map(function(v){return E('option',{value:v,text:v})})));s.value=st[k];s.addEventListener('change',function(){st[k]=s.value;draw()});return s}
    var seg=E('div',{'class':'rm-seg',role:'tablist','aria-label':'View'});[['list','List'],['board','Columns'],['timeline','Timeline']].forEach(function(v){var b=E('button',{type:'button',role:'tab','aria-selected':String(st.view===v[0]),'class':st.view===v[0]?'on':'',text:v[1],onclick:function(){st.view=v[0];save();draw();if(opts.onView)opts.onView(v[1])}});seg.appendChild(b)});
    var q=E('input',{type:'search',placeholder:'Search tasks','aria-label':'Search tasks'});q.value=st.q;q.addEventListener('input',function(){st.q=q.value;clearTimeout(q._t);q._t=setTimeout(function(){draw();var n=root.querySelector('input[type=search]');n.focus();n.setSelectionRange(n.value.length,n.value.length)},250)});
    var bar=E('div',{'class':'rm-bar'},[seg,sel('All products',products,'product'),sel('All statuses',statuses,'status'),q]);
    if(st.view==='board'){var g=E('select',{'aria-label':'Columns by'},[E('option',{value:'status',text:'Columns by status'}),E('option',{value:'sprint',text:'Columns by sprint'})]);g.value=st.group;g.addEventListener('change',function(){st.group=g.value;save();draw()});bar.appendChild(g)}
    root.appendChild(bar);var items=filtered();
    if(!items.length){root.appendChild(E('div',{'class':'rm-empty',text:all.length?'No tasks match these filters.':'The roadmap is empty for now.'}));return}
    root.appendChild(st.view==='board'?board(items):st.view==='timeline'?timeline(items):list(items))}
  draw();
}
"""
