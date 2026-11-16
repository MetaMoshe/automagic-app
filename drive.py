"""Folders in the client drive, shared by the admin client page and the client portal.

Folders live in `drive_folders` (nested through parent_id); a file's folder is client_files.folder_id
(NULL = the top of the drive). The page gets the whole tree as JSON and the widget (WIDGET_JS) renders
it, uploads into the open folder, and calls the op endpoint to create, rename, delete and move.

Rules: the admin can do everything. A client can create folders, rename or delete the folders they
created, and move any drive file or folder they're allowed to; they can delete only files they uploaded.
A folder is deleted only when empty.
"""
import json

import server as S

MAX_DEPTH = 6


def init():
    pk = "SERIAL PRIMARY KEY" if S.DATABASE_URL else "INTEGER PRIMARY KEY AUTOINCREMENT"
    with S.app.app_context():
        S.q(f"CREATE TABLE IF NOT EXISTS drive_folders (id {pk}, client_id INTEGER NOT NULL, parent_id INTEGER, "
            "name TEXT NOT NULL, created TEXT NOT NULL, created_by TEXT)")
        S.q("CREATE INDEX IF NOT EXISTS drive_folders_client ON drive_folders (client_id)")
        if S.DATABASE_URL:
            S.q("ALTER TABLE client_files ADD COLUMN IF NOT EXISTS folder_id INTEGER")
        elif "folder_id" not in [r[1] for r in S.q("PRAGMA table_info(client_files)")]:
            S.q("ALTER TABLE client_files ADD COLUMN folder_id INTEGER")
        if not S.DATABASE_URL:
            S.db().commit()


def _commit():
    if not S.DATABASE_URL:
        S.db().commit()


def folders(cid):
    return [{"id": r[0], "parent": r[1], "name": r[2], "by": r[3] or "admin"}
            for r in S.q("SELECT id, parent_id, name, created_by FROM drive_folders WHERE client_id = ? ORDER BY LOWER(name)", (cid,))]


def tree(cid, who):
    """Everything the widget needs. `who` is 'admin' or 'client'."""
    base = f"/admin/clients/{cid}/files/" if who == "admin" else "/portal/files/"
    files = [{"id": r[0], "folder": r[1], "name": r[2], "size": r[3] or 0, "at": (r[4] or "")[:10], "by": r[5] or "admin",
              "url": base + str(r[0]), "del": who == "admin" or r[5] == "client"}
             for r in S.q("SELECT id, folder_id, name, size, at, uploaded_by FROM client_files WHERE client_id = ? AND kind = 'drive' "
                          "ORDER BY LOWER(name)", (cid,))]
    fs = folders(cid)
    for f in fs:
        f["edit"] = who == "admin" or f["by"] == "client"
    return {"folders": fs, "files": files, "who": who}


def folder_ok(cid, fid):
    """fid if it is a folder of this client, None for the top level; raises ValueError otherwise."""
    if fid in (None, "", 0, "0"):
        return None
    try:
        fid = int(fid)
    except (TypeError, ValueError):
        raise ValueError("Unknown folder.")
    if not S.q("SELECT id FROM drive_folders WHERE id = ? AND client_id = ?", (fid, cid), one=True):
        raise ValueError("That folder no longer exists.")
    return fid


def _ancestors(cid, fid):
    parent = {f["id"]: f["parent"] for f in folders(cid)}
    out = []
    while fid is not None and fid not in out and len(out) < 50:
        out.append(fid)
        fid = parent.get(fid)
    return out


def _name(v):
    name = " ".join(str(v or "").replace("/", " ").replace("\\", " ").split())[:80]
    if not name:
        raise ValueError("Give the folder a name.")
    return name


def op(cid, who, d):
    """Run one drive operation. Returns an error string, or None when it worked."""
    try:
        kind = d.get("op")
        if kind == "mkdir":
            parent = folder_ok(cid, d.get("parent"))
            if parent and len(_ancestors(cid, parent)) >= MAX_DEPTH:
                return f"Folders can be nested up to {MAX_DEPTH} levels."
            name = _name(d.get("name"))
            if S.q("SELECT id FROM drive_folders WHERE client_id = ? AND COALESCE(parent_id, 0) = ? AND LOWER(name) = ?",
                   (cid, parent or 0, name.lower()), one=True):
                return "There's already a folder with that name here."
            S.q("INSERT INTO drive_folders (client_id, parent_id, name, created, created_by) VALUES (?, ?, ?, ?, ?)",
                (cid, parent, name, S.now(), who))
        elif kind in ("rename", "rmdir"):
            fid = folder_ok(cid, d.get("id"))
            if fid is None:
                return "Unknown folder."
            f = next(x for x in folders(cid) if x["id"] == fid)
            if who != "admin" and f["by"] != "client":
                return "This folder was created by your consultant, so only they can change it."
            if kind == "rename":
                S.q("UPDATE drive_folders SET name = ? WHERE id = ? AND client_id = ?", (_name(d.get("name")), fid, cid))
            else:
                if (S.q("SELECT id FROM drive_folders WHERE parent_id = ? AND client_id = ? LIMIT 1", (fid, cid), one=True)
                        or S.q("SELECT id FROM client_files WHERE folder_id = ? AND client_id = ? LIMIT 1", (fid, cid), one=True)):
                    return "Move or delete what's inside the folder first."
                S.q("DELETE FROM drive_folders WHERE id = ? AND client_id = ?", (fid, cid))
        elif kind == "move":
            to = folder_ok(cid, d.get("to"))
            above = _ancestors(cid, to)
            for fid in [int(x) for x in (d.get("folders") or [])][:200]:
                fid = folder_ok(cid, fid)
                if fid in above:
                    return "A folder can't be moved inside itself."
                S.q("UPDATE drive_folders SET parent_id = ? WHERE id = ? AND client_id = ?", (to, fid, cid))
            for fid in [int(x) for x in (d.get("files") or [])][:500]:
                S.q("UPDATE client_files SET folder_id = ? WHERE id = ? AND client_id = ? AND kind = 'drive'", (to, fid, cid))
        else:
            return "Unknown action."
    except ValueError as e:
        return str(e)
    except StopIteration:
        return "Unknown folder."
    _commit()
    return None


def tree_json(cid, who):
    return json.dumps(tree(cid, who)).replace("</", "<\\/")


# The widget. Mount with: amDrive(el, {tree, opUrl, treeUrl, uploadUrl, label, onEvent})
WIDGET_CSS = """
.dv{--dl:var(--line);--dm:var(--muted);border:1px solid var(--dl);border-radius:12px;background:var(--surface);overflow:hidden}
.dv-bar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:12px 14px;border-bottom:1px solid var(--dl)}
.dv-crumbs{display:flex;align-items:center;gap:4px;flex-wrap:wrap;min-width:0;flex:1;font-size:14.5px}
.dv-crumbs button{background:none;border:0;padding:4px 6px;border-radius:6px;color:var(--cyan);font:inherit;cursor:pointer}
.dv-crumbs button[aria-current]{color:var(--ink);font-weight:600;cursor:default}
.dv-crumbs button.over{background:rgba(127,207,224,.14)}
.dv-crumbs span{color:var(--dm)}
.dv-act{display:flex;gap:8px;flex-wrap:wrap}
.dv-act button,.dv-row button,.dv-sel button{font:500 13px Inter,system-ui,sans-serif;padding:7px 12px;border-radius:8px;border:1px solid var(--dl);background:transparent;color:var(--ink);cursor:pointer;white-space:nowrap}
.dv-act button.pri{background:var(--cyan);border-color:var(--cyan);color:#06202a;font-weight:600}
.dv-act button:hover,.dv-row button:hover,.dv-sel button:hover{border-color:var(--cyan)}
.dv-sel{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:10px 14px;background:rgba(127,207,224,.07);border-bottom:1px solid var(--dl);font-size:14px}
.dv-sel select,.dv-row select{font:13px Inter,system-ui,sans-serif;color:var(--ink);background:var(--bg);border:1px solid var(--dl);border-radius:8px;padding:6px 8px;max-width:220px}
.dv-list{list-style:none;margin:0;padding:0;min-height:90px;position:relative}
.dv-list.over{background:rgba(127,207,224,.06);outline:2px dashed var(--cyan);outline-offset:-6px}
.dv-row{display:grid;grid-template-columns:22px 36px minmax(0,1fr) auto;gap:10px;align-items:center;padding:9px 14px;border-top:1px solid var(--dl)}
.dv-row:first-child{border-top:0}
.dv-row.over{background:rgba(127,207,224,.12)}
.dv-row[draggable=true]{cursor:grab}
.dv-ic{width:34px;height:34px;border-radius:8px;display:grid;place-items:center;font:600 9.5px "IBM Plex Mono",monospace;background:rgba(127,207,224,.12);color:var(--cyan);text-transform:uppercase}
.dv-ic.fold{background:rgba(240,201,135,.14);color:#F0C987}
.dv-nm{min-width:0}.dv-nm a,.dv-nm button.open{color:var(--ink);text-decoration:none;font:500 14.5px Inter,system-ui,sans-serif;background:none;border:0;padding:0;cursor:pointer;text-align:left;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:block;max-width:100%}
.dv-nm a:hover,.dv-nm button.open:hover{color:var(--cyan)}
.dv-nm small{display:block;color:var(--dm);font-size:12.5px}
.dv-ops{display:flex;gap:6px;align-items:center}
.dv-row button.danger{color:#F2B8B5;border-color:#5a2a2a}
.dv-empty{padding:26px 14px;text-align:center;color:var(--dm);font-size:14px}
.dv-msg{padding:8px 14px;font-size:13.5px;color:#F2B8B5;border-bottom:1px solid var(--dl)}.dv-msg:empty{display:none}
.dv-msg.ok{color:#7FD3A6}
.dv-hint{padding:9px 14px;border-top:1px solid var(--dl);color:var(--dm);font-size:12.5px}
.dv input[type=checkbox]{width:16px;height:16px;accent-color:var(--cyan)}
@media (max-width:640px){.dv-row{grid-template-columns:22px 32px minmax(0,1fr)}.dv-ops{grid-column:3;flex-wrap:wrap}}
"""

WIDGET_JS = r"""
function amDrive(root, o){
  var T=o.tree, cur=null, sel={}, busy=false, picker=document.createElement('input');
  picker.type='file'; picker.multiple=true; picker.hidden=true; root.appendChild(picker);
  try{ var saved=sessionStorage.getItem('am-drive-'+o.key); if(saved) cur=JSON.parse(saved); }catch(e){}
  function E(tag,attrs,kids){var e=document.createElement(tag);for(var k in attrs||{}){if(k==='text')e.textContent=attrs[k];else if(k.slice(0,2)==='on')e.addEventListener(k.slice(2),attrs[k]);else e.setAttribute(k,attrs[k])}
    (kids||[]).forEach(function(c){if(c)e.appendChild(typeof c==='string'?document.createTextNode(c):c)});return e}
  function folder(id){for(var i=0;i<T.folders.length;i++)if(T.folders[i].id===id)return T.folders[i];return null}
  function path(id){var p=[],f=folder(id),n=0;while(f&&n++<50){p.unshift(f);f=folder(f.parent)}return p}
  function kids(id){return T.folders.filter(function(f){return (f.parent||null)===id})}
  function filesIn(id){return T.files.filter(function(f){return (f.folder||null)===id})}
  function count(id){return kids(id).length+filesIn(id).length}
  function size(n){return n>=1048576?(n/1048576).toFixed(1)+' MB':Math.max(1,Math.round(n/1024))+' KB'}
  function ext(n){var m=/\.([a-z0-9]{1,5})$/i.exec(n);return m?m[1]:'file'}
  function go(id){cur=id;sel={};try{sessionStorage.setItem('am-drive-'+o.key,JSON.stringify(id))}catch(e){}render();if(o.onEvent)o.onEvent('open-folder',id?folder(id).name:(o.label||'Drive'))}
  var note={t:'',ok:false};
  function msg(t,ok){note={t:t||'',ok:!!ok};var m=root.querySelector('.dv-msg');if(m){m.textContent=note.t;m.classList.toggle('ok',note.ok)}}
  function refresh(){return fetch(o.treeUrl,{credentials:'same-origin',cache:'no-store'}).then(function(r){return r.json()}).then(function(t){T=t;if(cur&&!folder(cur))cur=null;render()})}
  function call(d,okText){if(busy)return;busy=true;msg('');
    return fetch(o.opUrl,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d),credentials:'same-origin'})
      .then(function(r){return r.json()}).then(function(j){busy=false;if(!j.ok){msg(j.error||'That did not work.');return}T=j.tree;if(cur&&!folder(cur))cur=null;render();if(okText)msg(okText,true);if(o.onEvent)o.onEvent(d.op,d.name||'',d)})
      .catch(function(){busy=false;msg('Something went wrong. Please try again.')})}
  function upload(files){if(!files||!files.length)return;var fd=new FormData();fd.append('folder',cur||'');fd.append('kind','drive');
    for(var i=0;i<files.length;i++)fd.append('file',files[i]);msg('Uploading '+files.length+(files.length===1?' file…':' files…'),true);
    fetch(o.uploadUrl,{method:'POST',body:fd,credentials:'same-origin'}).then(function(r){return r.json()})
      .then(function(j){msg(j.errors&&j.errors.length?j.errors.join(' '):'Uploaded.',!(j.errors&&j.errors.length));return refresh()})
      .catch(function(){msg('Upload failed. Please try again.')})}
  function mkdir(){var n=prompt('Folder name');if(n&&n.trim())call({op:'mkdir',name:n.trim(),parent:cur},'Folder created.')}
  function options(skip){var out=[{id:'',name:'Top of drive'}];(function walk(p,d){kids(p).forEach(function(f){if(skip&&skip.indexOf(f.id)>=0)return;out.push({id:f.id,name:new Array(d+1).join(' ')+f.name});walk(f.id,d+1)})})(null,0);return out}
  function moveSelect(label,skip,onPick){var s=E('select',{'aria-label':label},[E('option',{value:'_',text:label})]);
    options(skip).forEach(function(x){s.appendChild(E('option',{value:x.id,text:x.name}))});
    s.addEventListener('change',function(){if(s.value!=='_')onPick(s.value===''?null:+s.value)});return s}
  function drag(row,payload){row.setAttribute('draggable','true');row.addEventListener('dragstart',function(e){e.dataTransfer.setData('text/am-drive',JSON.stringify(payload));e.dataTransfer.effectAllowed='move'})}
  function dropTarget(el,to){el.addEventListener('dragover',function(e){if(e.dataTransfer.types.indexOf('text/am-drive')<0)return;e.preventDefault();e.stopPropagation();el.classList.add('over')});
    el.addEventListener('dragleave',function(){el.classList.remove('over')});
    el.addEventListener('drop',function(e){var d=e.dataTransfer.getData('text/am-drive');if(!d)return;e.preventDefault();e.stopPropagation();el.classList.remove('over');d=JSON.parse(d);
      call({op:'move',files:d.files||[],folders:d.folders||[],to:to},'Moved.')})}
  function render(){
    root.querySelectorAll('.dv').forEach(function(x){x.remove()});
    var crumbs=E('nav',{'class':'dv-crumbs','aria-label':'Folder path'}),top=E('button',{type:'button',text:o.label||'Drive',onclick:function(){go(null)}});
    if(cur===null)top.setAttribute('aria-current','page');dropTarget(top,null);crumbs.appendChild(top);
    path(cur).forEach(function(f,i,a){crumbs.appendChild(E('span',{text:'/'}));var b=E('button',{type:'button',text:f.name,onclick:function(){go(f.id)}});if(i===a.length-1)b.setAttribute('aria-current','page');dropTarget(b,f.id);crumbs.appendChild(b)});
    var bar=E('div',{'class':'dv-bar'},[crumbs,E('div',{'class':'dv-act'},[E('button',{type:'button',text:'New folder',onclick:mkdir}),E('button',{type:'button','class':'pri',text:'Upload files',onclick:function(){picker.click()}})])]);
    var ids=Object.keys(sel).filter(function(k){return sel[k]}),selBar=null;
    if(ids.length){var fids=ids.filter(function(k){return k[0]==='f'}).map(function(k){return +k.slice(1)}),dids=ids.filter(function(k){return k[0]==='d'}).map(function(k){return +k.slice(1)});
      selBar=E('div',{'class':'dv-sel'},[E('b',{text:ids.length+' selected'}),moveSelect('Move to…',dids,function(to){call({op:'move',files:fids,folders:dids,to:to},'Moved.')}),E('button',{type:'button',text:'Clear',onclick:function(){sel={};render()}})])}
    var list=E('ul',{'class':'dv-list'}),fs=kids(cur),fl=filesIn(cur);
    fs.forEach(function(f){var n=count(f.id),ck=E('input',{type:'checkbox','aria-label':'Select folder '+f.name});ck.checked=!!sel['d'+f.id];ck.addEventListener('change',function(){sel['d'+f.id]=ck.checked;render()});
      var ops=E('span',{'class':'dv-ops'});
      if(f.edit){ops.appendChild(E('button',{type:'button',text:'Rename',onclick:function(){var v=prompt('Rename folder',f.name);if(v&&v.trim()&&v.trim()!==f.name)call({op:'rename',id:f.id,name:v.trim()},'Renamed.')}}));
        var del=E('button',{type:'button',text:'Delete',onclick:function(){if(n){msg('Move or delete what’s inside the folder first.');return}if(del.dataset.armed){call({op:'rmdir',id:f.id},'Folder deleted.')}else{del.dataset.armed=1;del.textContent='Confirm';del.classList.add('danger')}}});ops.appendChild(del)}
      var row=E('li',{'class':'dv-row'},[ck,E('span',{'class':'dv-ic fold','aria-hidden':'true',text:'DIR'}),E('span',{'class':'dv-nm'},[E('button',{type:'button','class':'open',text:f.name,onclick:function(){go(f.id)}}),E('small',{text:n+(n===1?' item':' items')})]),ops]);
      drag(row,{folders:[f.id]});dropTarget(row,f.id);list.appendChild(row)});
    fl.forEach(function(f){var ck=E('input',{type:'checkbox','aria-label':'Select '+f.name});ck.checked=!!sel['f'+f.id];ck.addEventListener('change',function(){sel['f'+f.id]=ck.checked;render()});
      var ops=E('span',{'class':'dv-ops'},[moveSelect('Move…',null,function(to){call({op:'move',files:[f.id],to:to},'Moved.')})]);
      if(f.del){var del=E('button',{type:'button',text:'Delete',onclick:function(){if(del.dataset.armed){fetch(o.delUrl(f.id),{method:'POST',credentials:'same-origin'}).then(function(){msg('File deleted.',true);if(o.onEvent)o.onEvent('delete',f.name);return refresh()})}else{del.dataset.armed=1;del.textContent='Confirm';del.classList.add('danger')}}});ops.appendChild(del)}
      var by=T.who==='admin'?(f.by==='client'?'From client':'From you'):(f.by==='client'?'You':'Automagic');
      var row=E('li',{'class':'dv-row'},[ck,E('span',{'class':'dv-ic','aria-hidden':'true',text:ext(f.name)}),E('span',{'class':'dv-nm'},[E('a',{href:f.url,target:'_blank',rel:'noopener',text:f.name}),E('small',{text:by+' · '+size(f.size)+' · '+f.at})]),ops]);
      drag(row,{files:[f.id]});list.appendChild(row)});
    if(!fs.length&&!fl.length)list.appendChild(E('li',{'class':'dv-empty',text:cur===null?'The drive is empty. Drop files here or create a folder.':'This folder is empty. Drop files here to upload them into it.'}));
    list.addEventListener('dragover',function(e){if(e.dataTransfer.types.indexOf('Files')<0)return;e.preventDefault();list.classList.add('over')});
    list.addEventListener('dragleave',function(e){if(!list.contains(e.relatedTarget))list.classList.remove('over')});
    list.addEventListener('drop',function(e){if(!e.dataTransfer.files.length)return;e.preventDefault();list.classList.remove('over');upload(e.dataTransfer.files)});
    root.appendChild(E('div',{'class':'dv'},[bar,E('p',{'class':'dv-msg'+(note.ok?' ok':''),role:'status',text:note.t}),selBar,list,E('p',{'class':'dv-hint',text:'Drag files onto a folder or the path above to move them, or drop files from your computer to upload them here. Up to 25 MB per file.'})]));
  }
  picker.addEventListener('change',function(){upload(picker.files);picker.value=''});
  if(cur!==null&&!folder(cur))cur=null;
  render();
  return {refresh:refresh};
}
"""
