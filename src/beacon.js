(function(){
// Automagic visitor analytics: first-party, no cookies, no third parties.
// One "view" event when the page opens, then "ping" updates with active
// reading time, scroll depth and the page sections the visitor actually read.
var E='/api/event',ls=null,vid='',sid='',now=Date.now();
try{ls=window.localStorage}catch(e){}
function rnd(){return Math.random().toString(36).slice(2)+Date.now().toString(36)}
try{vid=ls.getItem('am-vid');if(!vid){vid=rnd();ls.setItem('am-vid',vid)}
  // A session ends after 30 minutes without activity.
  var s=JSON.parse(ls.getItem('am-sid')||'null');
  sid=s&&now-s.t<18e5?s.id:rnd();ls.setItem('am-sid',JSON.stringify({id:sid,t:now}))}catch(e){vid=vid||'';sid=sid||''}
var pv=rnd(),q=new URLSearchParams(location.search);
function send(d){d=JSON.stringify(d);try{if(navigator.sendBeacon&&navigator.sendBeacon(E,d))return}catch(e){}
  try{fetch(E,{method:'POST',body:d,keepalive:true}).catch(function(){})}catch(e){}}
send({t:'view',path:location.pathname,ref:document.referrer,vid:vid,sid:sid,pv:pv,
  utm:[q.get('utm_source'),q.get('utm_medium'),q.get('utm_campaign')].map(function(x){return(x||'').slice(0,60)}).join('|')});

var active=0,maxScroll=0,last=now,secs=[],seen={},dirty=false;
function label(el){if(el.querySelector('h1'))return 'Top of page';var h=el.querySelector('h2,h3');var t=(h&&h.textContent||el.getAttribute('aria-label')||el.id||'').replace(/\s+/g,' ').trim();return t.slice(0,60)}
function collect(){secs=[];var all=document.querySelectorAll('main section, body > section, section.sec, section.blk');
  for(var i=0;i<all.length&&secs.length<40;i++){var el=all[i];if(el.parentElement&&el.parentElement.closest('section'))continue;
    var n=label(el);if(n&&secs.every(function(x){return x.n!==n}))secs.push({el:el,n:n,s:0})}}
function depth(){var d=document.documentElement,h=Math.max(d.scrollHeight,document.body.scrollHeight);
  var p=h<=innerHeight?100:Math.round((scrollY+innerHeight)/h*100);if(p>maxScroll){maxScroll=Math.min(100,p);dirty=true}}
['scroll','mousemove','keydown','touchstart','click','wheel'].forEach(function(e){addEventListener(e,function(){last=Date.now()},{passive:true})});
addEventListener('scroll',depth,{passive:true});
// Every second the tab is visible and the visitor was active in the last minute counts as reading time.
// A section counts as read once it filled a good part of the screen for 3 seconds.
setInterval(function(){if(document.visibilityState!=='visible'||Date.now()-last>6e4)return;
  active++;dirty=true;if(!secs.length&&active<5)collect();
  for(var i=0;i<secs.length;i++){var r=secs[i].el.getBoundingClientRect(),vis=Math.min(r.bottom,innerHeight)-Math.max(r.top,0);
    if(vis>=Math.min(innerHeight*.45,r.height*.6)&&++secs[i].s>=3)seen[secs[i].n]=1}
  if(active%15===0)flush()},1000);
function flush(){if(!dirty)return;dirty=false;try{ls.setItem('am-sid',JSON.stringify({id:sid,t:Date.now()}))}catch(e){}
  send({t:'ping',vid:vid,pv:pv,active:active,scroll:maxScroll,
    sections:secs.filter(function(x){return seen[x.n]}).map(function(x){return x.n})})}
addEventListener('load',function(){collect();depth()});
document.addEventListener('visibilitychange',function(){if(document.visibilityState==='hidden')flush()});
addEventListener('pagehide',flush);
})();
