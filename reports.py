"""Project reports: weekly (or any) written reports per client, written in the admin CRM and read in the portal.

Stored in `reports`. The body is plain text with light Markdown (see render()); drafts are only visible
in the admin, published reports appear under Project reports in the client's portal.
"""
import html
import re

import server as S

FIELDS = ["title", "period", "body"]


def init():
    pk = "SERIAL PRIMARY KEY" if S.DATABASE_URL else "INTEGER PRIMARY KEY AUTOINCREMENT"
    with S.app.app_context():
        S.q(f"CREATE TABLE IF NOT EXISTS reports (id {pk}, client_id INTEGER NOT NULL, created TEXT NOT NULL, updated TEXT, "
            "title TEXT NOT NULL, period TEXT, body TEXT, status TEXT DEFAULT 'draft', published_at TEXT, read_client TEXT)")
        S.q("CREATE INDEX IF NOT EXISTS reports_client ON reports (client_id)")
        if not S.DATABASE_URL:
            S.db().commit()


KEYS = ["id", "client_id", "created", "updated", "title", "period", "body", "status", "published_at", "read_client"]


def for_client(cid, published_only=False):
    sql = f"SELECT {', '.join(KEYS)} FROM reports WHERE client_id = ?"
    if published_only:
        sql += " AND status = 'published'"
    rows = [dict(zip(KEYS, r)) for r in S.q(sql, (cid,))]
    rows.sort(key=lambda r: (r["period"] or r["created"][:10], r["id"]), reverse=True)
    return rows


def get(cid, rid):
    r = S.q(f"SELECT {', '.join(KEYS)} FROM reports WHERE id = ? AND client_id = ?", (rid, cid), one=True)
    return dict(zip(KEYS, r)) if r else None


def unread(cid):
    return S.q("SELECT COUNT(*) FROM reports WHERE client_id = ? AND status = 'published' AND read_client IS NULL", (cid,), one=True)[0]


# ---------------------------------------------------------------- light Markdown
# Supported: # headings, **bold**, *italic*, `code`, [links](https://...), - and 1. lists (nested by indent),
# > quotes, --- rules, and | pipe | tables |. Everything is HTML-escaped first, so a report can't inject markup.
def _inline(t):
    t = html.escape(t, quote=False)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", t)
    t = re.sub(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])", r"<em>\1</em>", t)

    def link(m):
        url = html.unescape(m.group(2))
        if not re.match(r"^(https?://|mailto:)", url):
            return m.group(0)
        return f'<a href="{html.escape(url)}" target="_blank" rel="noopener">{m.group(1)}</a>'
    return re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, t)


def _cells(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _untoggle(lines):
    """Notion pastes a toggle as "- Title" with the whole block indented under it. Turn that into a
    heading followed by the block, so tables and sub-headings inside it render normally."""
    out, i = [], 0
    while i < len(lines):
        raw = lines[i]
        m = re.match(r"^(\s*)[-*+]\s+(.*)$", raw)
        if m:
            n = len(m.group(1))
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            nxt = lines[j] if j < len(lines) else ""
            ind = len(nxt) - len(nxt.lstrip(" "))
            if nxt.strip() and ind > n and not re.match(r"^[-*+]\s|^\d+[.)]\s", nxt.strip()):
                out.append(" " * n + "## " + m.group(2))
                k = i + 1
                while k < len(lines) and (not lines[k].strip() or len(lines[k]) - len(lines[k].lstrip(" ")) > n):
                    out.append(lines[k][ind - n:] if lines[k].strip() else "")
                    k += 1
                i = k
                continue
        out.append(raw)
        i += 1
    return out


def render(text):
    lines = _untoggle((text or "").replace("\r\n", "\n").replace("\t", "    ").split("\n"))
    out, para, i = [], [], 0
    stack = []  # open lists: (indent, tag)

    def close_para():
        if para:
            out.append("<p>" + "<br>".join(_inline(x) for x in para) + "</p>")
            para.clear()

    def close_lists(to=-1):
        while stack and stack[-1][0] > to:
            out.append(f"</li></{stack.pop()[1]}>")

    while i < len(lines):
        raw = lines[i]
        line = raw.strip()
        indent = len(raw) - len(raw.lstrip(" "))
        if not line:
            close_para()
            i += 1
            continue
        if line.startswith("|"):
            close_para()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i].strip())
                i += 1
            sep = [n for n, r in enumerate(rows) if re.fullmatch(r"\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?", r)]
            body = [r for n, r in enumerate(rows) if n not in sep]
            head = body[0] if body and sep and sep[0] == 1 else None
            if head and any(len(c) > 30 for c in _cells(head)):
                head = None  # a key/value table pasted with its first row as header: keep that row as content
            t = ['<div class="rtw"><table>']
            if head:
                t.append("<thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in _cells(head)) + "</tr></thead>")
                body = body[1:]
            t.append("<tbody>" + "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in _cells(r)) + "</tr>" for r in body) + "</tbody></table></div>")
            out.append("".join(t))
            continue
        m = re.match(r"^([-*+]|\d+[.)])\s+(.*)$", line)
        if m:
            close_para()
            tag = "ol" if m.group(1)[0].isdigit() else "ul"
            if not stack or indent > stack[-1][0]:
                out.append(f"<{tag}><li>")
                stack.append((indent, tag))
            else:
                close_lists(indent)
                if stack and stack[-1][1] != tag:
                    out.append(f"</li></{stack.pop()[1]}>")
                if not stack:
                    out.append(f"<{tag}><li>")
                    stack.append((indent, tag))
                else:
                    out.append("</li><li>")
            out.append(_inline(m.group(2)))
            i += 1
            continue
        if stack and indent > stack[-1][0]:
            # continuation text inside a list item
            out.append("<br>" + _inline(line))
            i += 1
            continue
        close_lists()
        h = re.match(r"^(#{1,4})\s+(.*)$", line)
        if h:
            close_para()
            lvl = min(len(h.group(1)) + 1, 5)  # # -> h2 inside the page
            out.append(f"<h{lvl}>{_inline(h.group(2))}</h{lvl}>")
        elif re.fullmatch(r"(-\s*){3,}|(\*\s*){3,}|(_\s*){3,}", line):
            close_para()
            out.append("<hr>")
        elif line.startswith(">"):
            close_para()
            out.append(f"<blockquote>{_inline(line.lstrip('> '))}</blockquote>")
        elif re.fullmatch(r"\*\*[^*]+\*\*", line):
            # a line that is all bold reads as a sub-heading
            close_para()
            out.append(f"<h3>{_inline(line[2:-2])}</h3>")
        else:
            para.append(line)
        i += 1
    close_para()
    close_lists()
    return "".join(out)


CSS = """
.rbody{font-size:15.5px;line-height:1.65;overflow-wrap:anywhere}
.rbody h2,.rbody h3,.rbody h4,.rbody h5{margin:26px 0 10px;line-height:1.3}.rbody h2{font-size:22px}.rbody h3{font-size:18px}.rbody h4,.rbody h5{font-size:16px}
.rbody>:first-child{margin-top:0}
.rbody p{margin:0 0 12px}.rbody ul,.rbody ol{margin:0 0 14px;padding-left:22px}.rbody li{margin:4px 0}
.rbody code{font:13.5px "IBM Plex Mono",ui-monospace,monospace;background:rgba(127,207,224,.1);padding:1px 6px;border-radius:5px}
.rbody blockquote{margin:0 0 14px;padding:4px 0 4px 14px;border-left:3px solid var(--cyan);color:var(--muted)}
.rbody hr{border:0;border-top:1px solid var(--line);margin:22px 0}
.rbody .rtw{overflow-x:auto;margin:10px 0 18px;border:1px solid var(--line);border-radius:10px}
.rbody table{border-collapse:collapse;width:100%;font-size:14.5px}
.rbody th,.rbody td{white-space:normal;padding:10px 14px;border-top:1px solid var(--line);text-align:left;vertical-align:top}
.rbody tr:first-child td,.rbody th{border-top:0}
.rbody th{font:600 12px "IBM Plex Mono",monospace;letter-spacing:.05em;text-transform:uppercase;color:var(--muted);background:rgba(127,207,224,.08)}
.rbody tbody td:first-child{font-weight:600;white-space:nowrap;width:1%}
@media (max-width:640px){.rbody tbody td:first-child{white-space:normal;width:auto;min-width:110px}}
"""
