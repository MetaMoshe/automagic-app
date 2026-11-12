"""Visitor analytics in the admin.

  /admin                          site-wide: traffic, engagement (time, scroll, sessions, bounce), pages,
                                  entry and exit pages, referrers, campaigns, devices, places
  /admin/analytics/page?path=...  one page: time, scroll reach, sections read, where people came from and went next
  /admin/visitors/journey?email=  one signed-in visitor: every session and page, with time and scroll

Data comes from the `visits` table. src/beacon.js records one row per page view and then updates it
with active reading time (seconds), scroll depth (%) and the sections read. Rows recorded before
engagement tracking existed have no session id and no time or scroll; they count as views only.
"""
import json
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from flask import redirect, render_template_string, request

import admin_ui as U
import server as S
from admin import bp, guard

PERIODS = (7, 30, 90)
COLS = "at, path, ref, vid, device, country, city, sid, active, scroll, sections, email, utm"


class V:
    """One page view."""
    __slots__ = COLS.replace(",", "").split()

    def __init__(self, row):
        for k, v in zip(self.__slots__, row):
            setattr(self, k, v)
        try:
            self.sections = json.loads(self.sections) if self.sections else []
        except ValueError:
            self.sections = []

    @property
    def tracked(self):
        return self.active is not None


def period():
    try:
        days = int(request.args.get("days", 30))
    except ValueError:
        days = 30
    return days if days in PERIODS else 30


def since(days):
    return (datetime.now(timezone.utc) - timedelta(days=days - 1)).strftime("%Y-%m-%d")


def load(where="", args=(), days=None):
    sql = f"SELECT {COLS} FROM visits WHERE 1=1"
    if days:
        sql += " AND at >= ?"
        args = (since(days),) + tuple(args)
    return [V(r) for r in S.q(sql + where + " ORDER BY at", args)]


def dur(s):
    if s is None:
        return "–"
    s = int(round(s))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {s % 3600 // 60:02d}m"


def median(xs):
    return statistics.median(xs) if xs else None


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def pct(n, d):
    return round(n / d * 100) if d else None


def top(dct, n=10):
    return sorted(dct.items(), key=lambda x: (-x[1], x[0]))[:n]


def sessions(views):
    """Session id -> page views in order (only views that carry a session id)."""
    out = defaultdict(list)
    for v in views:
        if v.sid:
            out[v.sid].append(v)
    return out


def daily(views, days):
    by_day = defaultdict(lambda: [0, set()])
    for v in views:
        by_day[v.at[:10]][0] += 1
        by_day[v.at[:10]][1].add(v.vid)
    today = datetime.now(timezone.utc).date()
    series = [((today - timedelta(days=i)).isoformat(),) for i in range(days - 1, -1, -1)]
    series = [(d, by_day[d][0], len(by_day[d][1])) for (d,) in series]
    return series, max([s[1] for s in series] + [1])


def engagement(views):
    t = [v for v in views if v.tracked]
    return {"tracked": len(t), "median": median([v.active for v in t]), "mean": mean([v.active for v in t]),
            "scroll": mean([v.scroll or 0 for v in t]), "end": pct(sum(1 for v in t if (v.scroll or 0) >= 90), len(t))}


def tracking_since():
    r = S.q("SELECT MIN(at) FROM visits WHERE sid IS NOT NULL", one=True)
    return r[0][:10] if r and r[0] else None


def fmt_utm(u):
    parts = (u or "").split("|") + ["", "", ""]
    src, med, camp = parts[:3]
    return " · ".join(x for x in (src or "?", med, camp) if x)


# ---------------------------------------------------------------- site-wide
@bp.get("/admin")
@bp.get("/admin/")
def analytics():
    r = guard()
    if r:
        return r
    days = period()
    views = load(days=days)
    series, peak = daily(views, days)
    sess = sessions(views)
    per = defaultdict(list)
    refs, devices, utms, entries, exits = (defaultdict(int) for _ in range(5))
    countries, cities = defaultdict(set), defaultdict(set)
    for v in views:
        per[v.path].append(v)
        countries[v.country or "Unknown"].add(v.vid)
        if v.city:
            cities[f"{v.city}, {v.country}"].add(v.vid)
        devices[v.device] += 1
    for vs in sess.values():
        entries[vs[0].path] += 1
        exits[vs[-1].path] += 1
        first = vs[0]
        refs[first.ref or "Direct or unknown"] += 1
        if first.utm:
            utms[fmt_utm(first.utm)] += 1
    for v in views:  # views from before sessions were tracked still count for referrers
        if not v.sid:
            refs[v.ref or "Direct or unknown"] += 1
    pages = []
    for path, vs in per.items():
        e = engagement(vs)
        pages.append({"path": path, "views": len(vs), "visitors": len({v.vid for v in vs}), **e})
    pages.sort(key=lambda p: (-p["views"], p["path"]))
    e = engagement(views)
    n_sess = len(sess)
    since_d = since(days)
    return render_template_string(
        ANALYTICS, days=days, periods=PERIODS, series=series, peak=peak, pages=pages[:25], dur=dur,
        views=len(views), visitors=len({v.vid for v in views}), e=e,
        sessions=n_sess, per_session=(sum(len(s) for s in sess.values()) / n_sess) if n_sess else None,
        sess_time=median([sum(v.active or 0 for v in s) for s in sess.values()]),
        bounce=pct(sum(1 for s in sess.values() if len(s) == 1), n_sess),
        returning=pct(len({v.vid for v in views if v.vid} & {x[0] for x in S.q(
            "SELECT DISTINCT vid FROM visits WHERE at < ?", (since_d,))}), len({v.vid for v in views})),
        entries=top(entries), exits=top(exits), refs=top(refs), utms=top(utms), devices=top(devices, 3),
        countries=top({k: len(v) for k, v in countries.items()}), cities=top({k: len(v) for k, v in cities.items()}),
        tracking_since=tracking_since(),
        access=S.q("SELECT COUNT(DISTINCT email) FROM access WHERE at >= ?", (since_d,), one=True)[0],
        tot_access=S.q("SELECT COUNT(DISTINCT email) FROM access", one=True)[0],
        leads=S.q("SELECT COUNT(*) FROM leads WHERE at >= ?", (since_d,), one=True)[0],
        tot_leads=S.q("SELECT COUNT(*) FROM leads", one=True)[0])


# ---------------------------------------------------------------- one page
@bp.get("/admin/analytics/page")
def analytics_page():
    r = guard()
    if r:
        return r
    path = request.args.get("path", "")
    if not path.startswith("/"):
        return redirect("/admin")
    days = period()
    views = load(days=days)
    mine = [v for v in views if v.path == path]
    series, peak = daily(mine, days)
    e = engagement(mine)
    t = [v for v in mine if v.tracked]
    reach = [(f"{p}%", pct(sum(1 for v in t if (v.scroll or 0) >= (90 if p == 100 else p)), len(t))) for p in (25, 50, 75, 100)]
    # Sections, in page order: the average position each one has in the readers' lists.
    count, pos = defaultdict(int), defaultdict(list)
    for v in t:
        for i, s in enumerate(v.sections):
            count[s] += 1
            pos[s].append(i)
    sections = sorted(count, key=lambda s: mean(pos[s]))
    sections = [(s, count[s], pct(count[s], len(t))) for s in sections]
    came, went = defaultdict(int), defaultdict(int)
    for vs in sessions(views).values():
        for i, v in enumerate(vs):
            if v.path != path:
                continue
            came[vs[i - 1].path if i else (("From " + v.ref) if v.ref else "Direct or unknown")] += 1
            went[vs[i + 1].path if i + 1 < len(vs) else "Left the site"] += 1
    people = defaultdict(list)
    for v in mine:
        if v.email:
            people[v.email].append(v)
    people = sorted(({"email": k, "views": len(vs), "time": sum(v.active or 0 for v in vs),
                      "scroll": max([v.scroll or 0 for v in vs]), "last": vs[-1].at} for k, vs in people.items()),
                    key=lambda p: p["last"], reverse=True)
    devices = defaultdict(int)
    for v in mine:
        devices[v.device] += 1
    return render_template_string(
        PAGE, path=path, days=days, periods=PERIODS, series=series, peak=peak, dur=dur, e=e,
        views=len(mine), visitors=len({v.vid for v in mine}), reach=reach, sections=sections,
        came=top(came), went=top(went), people=people[:50], devices=top(devices, 3), tracking_since=tracking_since())


# ---------------------------------------------------------------- one visitor
@bp.get("/admin/visitors/journey")
def journey():
    r = guard()
    if r:
        return r
    email = request.args.get("email", "").strip()
    if not email:
        return redirect("/admin/visitors")
    # Everything this browser did, including visits before the visitor gave their email.
    vids = [x[0] for x in S.q("SELECT DISTINCT vid FROM visits WHERE email = ? AND vid IS NOT NULL AND vid <> ''", (email,))]
    where, args = " AND (email = ?", [email]
    if vids:
        where += f" OR vid IN ({', '.join('?' * len(vids))})"
        args += vids
    views = load(where + ")", args)
    groups, order = defaultdict(list), []
    for v in views:
        key = v.sid or ("v:" + v.at[:13])  # older rows: group by hour
        if key not in groups:
            order.append(key)
        groups[key].append(v)
    leads = S.q("SELECT id, at, page, engagement FROM leads WHERE email = ? OR access_email = ? ORDER BY at", (email, email))
    sess = []
    for key in reversed(order):
        vs = groups[key]
        end = (datetime.strptime(vs[-1].at, "%Y-%m-%dT%H:%M:%SZ") + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        sess.append({"leads": [l for l in leads if vs[0].at <= l[1] <= end],"start": vs[0].at, "end": vs[-1].at, "views": vs, "time": sum(v.active or 0 for v in vs),
                     "tracked": any(v.tracked for v in vs), "ref": vs[0].ref, "utm": fmt_utm(vs[0].utm) if vs[0].utm else "",
                     "device": vs[0].device, "place": ", ".join(x for x in (vs[0].city, vs[0].country) if x)})
    access = S.q("SELECT MIN(at), COUNT(*) FROM access WHERE email = ?", (email,), one=True)
    pages = defaultdict(lambda: [0, 0])
    for v in views:
        pages[v.path][0] += 1
        pages[v.path][1] += v.active or 0
    return render_template_string(
        JOURNEY, email=email, sess=sess, dur=dur, views=len(views), time=sum(v.active or 0 for v in views),
        first=views[0].at if views else None, last=views[-1].at if views else None, access=access, leads=leads,
        pages=sorted(pages.items(), key=lambda x: (-x[1][1], -x[1][0])))


def people_time():
    """Email -> total active seconds, for the Access and visitors table."""
    return {e: t or 0 for e, t in S.q("SELECT email, SUM(active) FROM visits WHERE email IS NOT NULL GROUP BY email")}


# ---------------------------------------------------------------- templates
BARS = """{% macro bars(items, empty='No data yet.', link=False) %}{% if items %}<ul class="bars">{% for k, n in items %}<li><span>{% if link and k.startswith('/') %}<a href="/admin/analytics/page?path={{ k|urlencode }}&days={{ days }}">{{ k }}</a>{% else %}{{ k }}{% endif %}</span><span>{{ n }}</span><i><b style="width:{{ (n / items[0][1] * 100)|round }}%"></b></i></li>{% endfor %}</ul>{% else %}<p class="empty">{{ empty }}</p>{% endif %}{% endmacro %}"""

CSS = """<style>
.tile small{display:block;color:var(--muted);font-size:12.5px;margin-top:2px}
.sub{color:var(--muted);font-size:14px;margin:4px 0 0}
td.n,th.n{text-align:right;white-space:nowrap}td a{text-decoration:none}td a:hover{text-decoration:underline}
.meter{display:inline-block;width:56px;height:5px;border-radius:3px;background:var(--line);vertical-align:middle;margin-left:8px;overflow:hidden}.meter b{display:block;height:100%;background:var(--cyan)}
.reach{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;align-items:end;height:150px;margin-top:8px}
.reach div{display:flex;flex-direction:column;justify-content:flex-end;height:100%;text-align:center;gap:6px}
.reach i{display:block;background:var(--cyan);border-radius:4px 4px 0 0;min-height:2px;opacity:.85}
.reach span{font-size:12.5px;color:var(--muted)}.reach b{font:600 15px "Space Grotesk",sans-serif}
.sess{border:1px solid var(--line);border-radius:12px;background:var(--surface);margin:0 0 12px;overflow:hidden}
.sess header{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;padding:12px 16px;background:var(--cyan-soft)}
.sess header b{font-weight:600}.sess header span{color:var(--muted);font-size:13.5px}
.steps{list-style:none;margin:0;padding:6px 0}
.steps li{display:grid;grid-template-columns:62px minmax(0,1fr) auto;gap:12px;padding:8px 16px;align-items:start}
.steps time{font:500 12px "IBM Plex Mono",monospace;color:var(--muted);padding-top:2px}
.steps .pth{overflow-wrap:anywhere}.steps .pth small{display:block;color:var(--muted);font-size:12.5px;margin-top:2px}
.steps .st{color:var(--muted);font-size:13px;white-space:nowrap;text-align:right}
.steps li.lead{background:rgba(127,211,166,.07)}.steps li.lead .pth{color:#7FD3A6}
</style>"""

PERIOD_CHIPS = """<nav class="chips" aria-label="Period">{% for p in periods %}<a href="?{% if path is defined %}path={{ path|urlencode }}&{% endif %}days={{ p }}" class="{{ 'on' if p == days }}">Last {{ p }} days</a>{% endfor %}</nav>"""

NOTE = """<p class="mono" style="margin:32px 0 0;text-transform:none;letter-spacing:0">Time on page counts only seconds when the tab was open and the visitor had scrolled, clicked or moved within the last minute. A section counts as read after it filled a good part of the screen for 3 seconds. {% if tracking_since %}Time, scroll, sessions and sections are recorded from {{ tracking_since }}; earlier views count as views only.{% else %}Time, scroll, sessions and sections start recording with the next visits.{% endif %} Locations are approximate, from <a href="https://db-ip.com">IP Geolocation by DB-IP</a>. Times are UTC.</p>"""

CHART = """<div class="card"><div class="chart" role="img" aria-label="Daily page views for the last {{ days }} days">
{% for d, n, u in series %}<div style="height:{{ (n / peak * 100)|round(1) }}%" title="{{ d }}: {{ n }} views, {{ u }} visitors"></div>{% endfor %}
</div><div class="axis"><span>{{ series[0][0] }}</span><span>Today</span></div></div>"""

ANALYTICS = BARS + U.head("chart", "Analytics") + CSS + """
<div class="ph"><div><h2>Analytics</h2><p>Visits to the website and the portfolio. Your own visits while signed in to admin aren't counted.</p></div></div>
""" + PERIOD_CHIPS + """
<section><p class="mono">Traffic</p>
<div class="tiles">
<div class="tile"><span class="mono">Page views</span><b>{{ views }}</b><small>{{ visitors }} unique visitors, by browser</small></div>
<div class="tile"><span class="mono">Returning visitors</span><b>{{ returning ~ '%' if returning is not none else '–' }}</b><small>had visited before this period</small></div>
<a class="tile" href="/admin/visitors"><span class="mono">Access sign-ups</span><b>{{ access }}</b><small>{{ tot_access }} unique emails in total</small></a>
<a class="tile" href="/admin/leads"><span class="mono">Book a Call leads</span><b>{{ leads }}</b><small>{{ tot_leads }} in total</small></a>
</div></section>
<section><p class="mono">Engagement</p>
<div class="tiles">
<div class="tile"><span class="mono">Time on page</span><b>{{ dur(e.median) }}</b><small>median · average {{ dur(e.mean) }}</small></div>
<div class="tile"><span class="mono">Scroll depth</span><b>{{ (e.scroll|round|int ~ '%') if e.scroll is not none else '–' }}</b><small>average · {{ (e.end ~ '%') if e.end is not none else '–' }} read to the end</small></div>
<div class="tile"><span class="mono">Sessions</span><b>{{ sessions }}</b><small>{{ ('%.1f'|format(per_session)) if per_session else '–' }} pages each · median {{ dur(sess_time) }}</small></div>
<div class="tile"><span class="mono">Bounce rate</span><b>{{ (bounce ~ '%') if bounce is not none else '–' }}</b><small>sessions that saw one page only</small></div>
</div></section>
<section><h2 style="font-size:19px">Page views, last {{ days }} days <a href="/admin/export/visits.csv">Download CSV</a></h2>""" + CHART + """</section>
<section><h2 style="font-size:19px">Pages</h2><p class="sub" style="margin:-6px 0 12px">Click a page for its sections, scroll reach, and where people came from and went next.</p>
{% if pages %}<div class="tw"><table><thead><tr><th>Page</th><th class="n">Views</th><th class="n">Visitors</th><th class="n">Median time</th><th class="n">Avg scroll</th><th class="n">Read to end</th></tr></thead><tbody>
{% for p in pages %}<tr class="rowlink" onclick="location.href=this.querySelector('a').href"><td><a href="/admin/analytics/page?path={{ p.path|urlencode }}&days={{ days }}">{{ p.path }}</a></td><td class="n">{{ p.views }}</td><td class="n">{{ p.visitors }}</td><td class="n">{{ dur(p.median) }}</td>
<td class="n">{% if p.scroll is not none %}{{ p.scroll|round|int }}%<span class="meter"><b style="width:{{ p.scroll|round }}%"></b></span>{% else %}–{% endif %}</td><td class="n">{{ (p.end ~ '%') if p.end is not none else '–' }}</td></tr>{% endfor %}
</tbody></table></div>{% else %}<div class="card empty">No visits yet.</div>{% endif %}</section>
<section><div class="grid3">
<div class="card"><p class="mono">Entry pages · sessions</p>{{ bars(entries, 'No sessions yet.', True) }}</div>
<div class="card"><p class="mono">Exit pages · sessions</p>{{ bars(exits, 'No sessions yet.', True) }}</div>
<div class="card"><p class="mono">Referrers</p>{{ bars(refs, 'No visits yet.') }}</div>
</div></section>
<section><div class="grid3">
<div class="card"><p class="mono">Campaigns · sessions</p>{{ bars(utms, 'None yet. Add ?utm_source=linkedin&utm_campaign=… to links you share.') }}</div>
<div class="card"><p class="mono">Devices</p>{{ bars(devices, 'No visits yet.') }}</div>
<div class="card"><p class="mono">Countries · visitors</p>{{ bars(countries, 'No visits yet.') }}</div>
</div></section>
<section><div class="card"><p class="mono">Cities · visitors</p>{{ bars(cities, 'No city data yet.') }}</div></section>
""" + NOTE + U.FOOT

PAGE = BARS + U.head("chart", "Page analytics") + CSS + """
<p style="margin:18px 0 0"><a href="/admin?days={{ days }}">← Analytics</a></p>
<div class="ph"><div><h2>{{ path }}</h2><p><a href="{{ path }}" target="_blank" rel="noopener">Open page</a></p></div></div>
""" + PERIOD_CHIPS + """
<section><div class="tiles">
<div class="tile"><span class="mono">Views</span><b>{{ views }}</b><small>{{ visitors }} unique visitors</small></div>
<div class="tile"><span class="mono">Time on page</span><b>{{ dur(e.median) }}</b><small>median · average {{ dur(e.mean) }}</small></div>
<div class="tile"><span class="mono">Scroll depth</span><b>{{ (e.scroll|round|int ~ '%') if e.scroll is not none else '–' }}</b><small>average</small></div>
<div class="tile"><span class="mono">Read to the end</span><b>{{ (e.end ~ '%') if e.end is not none else '–' }}</b><small>of {{ e.tracked }} tracked views</small></div>
</div></section>
<section><h2 style="font-size:19px">Views, last {{ days }} days</h2>""" + CHART + """</section>
<section><div class="split">
<div class="card"><p class="mono">Sections read · share of views, in page order</p>
{% if sections %}<ul class="bars">{% for s, n, p in sections %}<li><span>{{ s }}</span><span>{{ p }}%</span><i><b style="width:{{ p }}%"></b></i></li>{% endfor %}</ul>
{% else %}<p class="empty">No section data yet.</p>{% endif %}</div>
<div class="card"><p class="mono">How far people scroll · share of views</p>
{% if e.tracked %}<div class="reach" role="img" aria-label="Share of views reaching each scroll depth">{% for k, p in reach %}<div><b>{{ p }}%</b><i style="height:{{ [p, 1]|max }}%"></i><span>{{ k }}</span></div>{% endfor %}</div>
{% else %}<p class="empty">No scroll data yet.</p>{% endif %}</div>
</div></section>
<section><div class="grid3">
<div class="card"><p class="mono">Came from</p>{{ bars(came, 'No sessions yet.', True) }}</div>
<div class="card"><p class="mono">Went next</p>{{ bars(went, 'No sessions yet.', True) }}</div>
<div class="card"><p class="mono">Devices</p>{{ bars(devices, 'No visits yet.') }}</div>
</div></section>
<section><h2 style="font-size:19px">Signed-in viewers</h2>
{% if people %}<div class="tw"><table><thead><tr><th>Email</th><th class="n">Views</th><th class="n">Time on page</th><th class="n">Furthest scroll</th><th>Last viewed (UTC)</th></tr></thead><tbody>
{% for p in people %}<tr class="rowlink" onclick="location.href=this.querySelector('a').href"><td><a href="/admin/visitors/journey?email={{ p.email|urlencode }}">{{ p.email }}</a></td><td class="n">{{ p.views }}</td><td class="n">{{ dur(p.time) }}</td><td class="n">{{ p.scroll }}%</td><td class="nw">{{ p.last[:16]|replace('T',' ') }}</td></tr>{% endfor %}
</tbody></table></div>{% else %}<div class="card empty">No signed-in viewers in this period.</div>{% endif %}</section>
""" + NOTE + U.FOOT

JOURNEY = U.head("visitors", "Visitor journey") + CSS + """
<p style="margin:18px 0 0"><a href="/admin/visitors">← Access and visitors</a></p>
<div class="ph"><div><h2>{{ email }}</h2><p><a href="mailto:{{ email }}">Email them</a>{% if first %} · first seen {{ first[:10] }}, last seen {{ last[:16]|replace('T',' ') }} UTC{% endif %}</p></div></div>
<section><div class="tiles">
<div class="tile"><span class="mono">Sessions</span><b>{{ sess|length }}</b><small>{{ views }} page views</small></div>
<div class="tile"><span class="mono">Time spent</span><b>{{ dur(time) }}</b><small>active reading time, all visits</small></div>
<div class="tile"><span class="mono">Signed up</span><b style="font-size:20px;margin:12px 0 6px">{{ access[0][:10] if access and access[0] else '–' }}</b><small>{{ 'for portfolio access' if access and access[0] else 'no access sign-up' }}</small></div>
<a class="tile" href="{{ '/admin/leads/' ~ leads[-1][0] if leads else '/admin/leads' }}"><span class="mono">Book a Call</span><b>{{ leads|length }}</b><small>{{ 'request' if leads|length == 1 else 'requests' }}</small></a>
</div></section>
<section><div class="split">
<div><h2 style="font-size:19px">Sessions, newest first</h2>
{% for s in sess %}<div class="sess"><header><b>{{ s.start[:10] }} · {{ s.start[11:16] }}</b><span>{{ s.views|length }} {{ 'page' if s.views|length == 1 else 'pages' }}{% if s.tracked %} · {{ dur(s.time) }}{% endif %} · {{ s.device }}{% if s.place %} · {{ s.place }}{% endif %}{% if s.ref %} · from {{ s.ref }}{% endif %}{% if s.utm %} · {{ s.utm }}{% endif %}</span></header>
<ol class="steps">{% for v in s.views %}<li><time>{{ v.at[11:16] }}</time><span class="pth"><a href="/admin/analytics/page?path={{ v.path|urlencode }}">{{ v.path }}</a>{% if v.sections %}<small>Read: {{ v.sections|join(' · ') }}</small>{% endif %}{% if not v.email %}<small>Before signing in</small>{% endif %}</span>
<span class="st">{% if v.tracked %}{{ dur(v.active) }} · {{ v.scroll or 0 }}% scrolled{% else %}view only{% endif %}</span></li>{% endfor %}
{% for l in s.leads %}<li class="lead"><time>{{ l[1][11:16] }}</time><span class="pth"><a href="/admin/leads/{{ l[0] }}">Booked a call</a>{% if l[2] %}<small>from {{ l[2] }}{% if l[3] %} · {{ l[3] }}{% endif %}</small>{% endif %}</span><span class="st"></span></li>{% endfor %}</ol></div>
{% else %}<div class="card empty">No visits recorded for this email.</div>{% endfor %}</div>
<div class="card"><p class="mono">Pages · time spent</p>{% if pages %}<ul class="bars">{% for p, x in pages %}<li><span><a href="/admin/analytics/page?path={{ p|urlencode }}">{{ p }}</a></span><span>{{ dur(x[1]) if x[1] else (x[0] ~ ' views') }}</span><i><b style="width:{{ ((x[1] / pages[0][1][1] * 100) if pages[0][1][1] else 100)|round }}%"></b></i></li>{% endfor %}</ul>{% else %}<p class="empty">Nothing yet.</p>{% endif %}
{% if leads %}<p class="mono" style="margin-top:20px">Book a Call</p><ul class="bars">{% for l in leads %}<li><span><a href="/admin/leads/{{ l[0] }}">{{ l[1][:16]|replace('T',' ') }}</a></span><span>{{ l[2] or '' }}</span></li>{% endfor %}</ul>{% endif %}</div>
</div></section>
<p class="mono" style="margin:32px 0 0;text-transform:none;letter-spacing:0">Includes this browser's visits before they gave their email. Time counts only active reading. Times are UTC.</p>
""" + U.FOOT
