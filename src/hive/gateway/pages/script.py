# ruff: noqa: E501  (inline JS)
"""The desk's one page script, inlined into every page and pinned by hash in the CSP."""

from __future__ import annotations

import base64
import hashlib

# Local time, live updates (SSE with a polling fallback), live tail and the alerts button.
# Pinned by hash in the CSP (see ``SCRIPT_CSP_HASH``); no server data is interpolated into
# it, so changing it here changes the hash with it.
SCRIPT = """
(function(){
var FB='Australia/Sydney',Z;
try{Z=Intl.DateTimeFormat().resolvedOptions().timeZone||FB;}catch(e){Z=FB;}
function f(o,d){try{return new Intl.DateTimeFormat('en-AU',Object.assign({timeZone:Z},o)).format(d);}
catch(e){return new Intl.DateTimeFormat('en-AU',Object.assign({timeZone:FB},o)).format(d);}}
function stamp(el){var d=new Date(el.getAttribute('datetime'));if(isNaN(d))return;
var s=(Date.now()-d.getTime())/1000,t,today=f({dateStyle:'short'},d)===f({dateStyle:'short'},new Date());
if(el.className==='reset'){t=f({hour:'numeric',minute:'2-digit'},d);if(!today)t=f({weekday:'short'},d)+' '+t;}
else if(el.className==='date')t=f({day:'numeric',month:'short',year:'numeric',hour:'numeric',minute:'2-digit'},d);
else if(s>=0&&s<60)t='just now';
else if(s>=0&&s<3600)t=Math.floor(s/60)+' min ago';
else if(s>=0&&s<21600)t=Math.floor(s/3600)+' h ago';
else{var h=f({hour:'numeric',minute:'2-digit'},d);
if(today)t='today '+h;
else t=f({weekday:'short',day:'numeric',month:'short'},d)+', '+h;}
el.textContent=t;el.title=f({dateStyle:'medium',timeStyle:'short'},d)+' ('+Z+')';}
function stamps(){var a=document.getElementsByTagName('time');for(var i=0;i<a.length;i++)stamp(a[i]);}
function sel(){var s=window.getSelection&&window.getSelection();return !!(s&&!s.isCollapsed);}
function busy(){var a=document.activeElement;if(sel())return true;
if(a&&/^(TEXTAREA|INPUT|SELECT)$/.test(a.tagName))return true;
if(document.querySelector('details[open],dialog[open],button.is-armed,form.is-sending,form.is-shown'))return true;
var x=document.querySelectorAll('textarea,input:not([type]),input[type=text],input[type=search]');
for(var i=0;i<x.length;i++)if(x[i].value)return true;
x=document.querySelectorAll('input[type=checkbox]');
for(var j=0;j<x.length;j++)if(x[j].checked!==x[j].defaultChecked)return true;
x=document.querySelectorAll('select');
for(var k=0;k<x.length;k++){var o=x[k].options[x[k].selectedIndex];if(o&&!o.defaultSelected)return true;}
return false;}
function load(cb){fetch(location.href,{credentials:'same-origin',cache:'no-store'})
.then(function(r){return r.ok?r.text():null;}).then(function(t){
if(t)cb(new DOMParser().parseFromString(t,'text/html'));}).catch(function(){});}
var b=document.body,every=+b.getAttribute('data-refresh'),poll=+b.getAttribute('data-poll');
var tail=b.getAttribute('data-tail'),mach=b.getAttribute('data-machine')!==null||b.getAttribute('data-signins')!==null,rq=b.getAttribute('data-run')!==null;
var th=document.getElementById('thread'),c=document.querySelector('main');
var last=th?th.innerHTML:c?c.textContent:null,live=false,want=false,es,seen=0;
function bottom(){if(th)th.scrollTop=th.scrollHeight;else window.scrollTo(0,document.documentElement.scrollHeight);}
function atBottom(){return th.scrollHeight-th.scrollTop-th.clientHeight<80;}
var jb=document.getElementById('jump');
function jump(on){if(jb)jb.hidden=!on;}
if(th&&jb){jb.onclick=function(){bottom();jump(false);};th.addEventListener('scroll',function(){if(atBottom())jump(false);});}
if(th)bottom();
var locals=[];
function hasRid(rid){var a=th.children;for(var i=0;i<a.length;i++)if(a[i].getAttribute('data-rid')===rid)return true;return false;}
function lsync(){for(var i=locals.length-1;i>=0;i--){var m=locals[i];
if(hasRid(m.rid)){if(m.el.parentNode)m.el.parentNode.removeChild(m.el);locals.splice(i,1);}
else if(m.el.parentNode!==th)th.appendChild(m.el);}}
function refreshThread(){if(document.hidden||sel())return;load(function(doc){
var n=doc.getElementById('thread');if(!n||sel())return;
if(n.innerHTML!==last){last=n.innerHTML;
var near=atBottom();
th.innerHTML=n.innerHTML;stamps();lsync();if(near)bottom();else jump(true);}else lsync();});}
function reviewSheetOnly(){var d=document.querySelectorAll('dialog[open]');
return d.length===1&&d[0].id==='rvs-sheet'&&!document.querySelector('details[open],.rv__x.is-armed,form.is-sending')&&!sel();}
function rsig(e){var a=e.querySelectorAll('a[href]'),h=[];
for(var i=0;i<a.length;i++)h.push(a[i].getAttribute('href'));return h.join(' ')+' '+e.textContent;}
function swapReviews(doc){var n=doc.querySelector('.rvs'),o=document.querySelector('.rvs');
if(!o)return;if(!n){o.parentNode.removeChild(o);return;}
if(rsig(n)===rsig(o))return;
var d=o.querySelector('dialog[open]'),sl=d?d.querySelector('.sheet__list'):null,st=sl?sl.scrollTop:0,
bl=o.querySelector('.rv__list'),bt=bl?bl.scrollTop:0;
o.outerHTML=n.outerHTML;var rn=doc.querySelectorAll('[data-rvn]'),ro=document.querySelectorAll('[data-rvn]');
for(var i=0;i<rn.length&&i<ro.length;i++)ro[i].textContent=rn[i].textContent;
var nl=document.querySelector('.rvs .rv__list');if(nl)nl.scrollTop=bt;
if(d){var nd=document.getElementById('rvs-sheet');
if(nd){nd.classList.add('is-restored');if(nd.showModal)nd.showModal();else nd.setAttribute('open','');
var l=nd.querySelector('.sheet__list');if(l)l.scrollTop=st;var x=nd.querySelector('[data-sheet-close]');if(x)x.focus();}
else sheetOpener=null;}}
function refreshMain(){if(document.hidden){want=true;return;}
if(reviewSheetOnly()){want=true;load(swapReviews);return;}
if(busy()){want=true;return;}
want=false;load(function(doc){
['qchip','fmchip'].forEach(function(id){var q=doc.getElementById(id),oq=document.getElementById(id);
if(q){var qt=q.getElementsByTagName('time');for(var i=0;i<qt.length;i++)stamp(qt[i]);}
if(q&&oq&&!oq.open&&q.outerHTML!==oq.outerHTML)oq.outerHTML=q.outerHTML;});
var m=doc.querySelector('main');if(!m||m.textContent===last||busy())return;last=m.textContent;
var y=window.scrollY,ps=panels();c.innerHTML=m.innerHTML;restore();panels(ps);stamps();showFlash();
window.scrollTo(0,y);});}
function panels(v){return [].map.call(c.querySelectorAll('.pn__b,.rv__list'),function(e,i){
if(v&&v[i])e.scrollTop=v[i];return e.scrollTop;});}
function refreshAny(){if(th)refreshThread();else refreshMain();}
function connect(){es=new EventSource('/events');
es.onopen=function(){live=true;seen=Date.now();};
es.onerror=function(){live=false;};
es.addEventListener('hello',function(){seen=Date.now();live=true;refreshAny();});
es.addEventListener('ping',function(){seen=Date.now();});
es.addEventListener('desk',function(){seen=Date.now();if(!th)refreshMain();});
es.addEventListener('machine',function(){seen=Date.now();if(mach)refreshMain();});
es.addEventListener('run',function(){seen=Date.now();if(rq)refreshMain();});
es.addEventListener('chat',function(){seen=Date.now();if(th)refreshThread();});}
if(poll&&th)setInterval(function(){if(!live)refreshThread();},poll);
else if(every&&c)setInterval(function(){if(!live||mach)refreshMain();},every);
if(!tail&&((poll&&th)||(every&&c))&&window.EventSource){connect();
setInterval(function(){if((live&&Date.now()-seen>45000)||es.readyState===2){live=false;es.close();connect();}
if(want&&!th&&!document.hidden&&!busy())refreshMain();},2000);
document.addEventListener('visibilitychange',function(){if(!document.hidden&&want&&!th)refreshMain();});}
if(tail){var pre=document.getElementById('tail'),st=document.getElementById('tail-status'),
url='/w/'+encodeURIComponent(tail)+'/out';
function tick(){if(document.hidden)return;fetch(url,{credentials:'same-origin',cache:'no-store'})
.then(function(r){return r.json();}).then(function(j){
if(!j.ok){st.textContent='Cannot read output: '+j.error;return;}
var near=pre.scrollHeight-pre.clientHeight-pre.scrollTop<120;
if(pre.textContent!==j.text){pre.textContent=j.text;if(near)pre.scrollTop=pre.scrollHeight;}
st.textContent='Live · updated '+new Date().toLocaleTimeString();})
.catch(function(){st.textContent='Connection lost, retrying…';});}
tick();setInterval(tick,3000);document.addEventListener('visibilitychange',tick);}
var ab=document.getElementById('alerts');
function key(s){s=s.replace(/-/g,'+').replace(/_/g,'/');s+='='.repeat((4-s.length%4)%4);
var r=atob(s),a=new Uint8Array(r.length);for(var i=0;i<r.length;i++)a[i]=r.charCodeAt(i);return a;}
function post(path,obj,csrf){return fetch(path,{method:'POST',credentials:'same-origin',
headers:{'content-type':'application/json','x-csrf':csrf},body:JSON.stringify(obj)});}
function note(t){var an=document.getElementById('alerts-note');if(an)an.textContent=t;}
if(ab&&'serviceWorker' in navigator&&'PushManager' in window&&'Notification' in window){
navigator.serviceWorker.register('/sw.js').then(function(reg){
function show(on){ab.hidden=false;ab.textContent=on?'Alerts on':'Alerts off';ab.setAttribute('aria-pressed',on?'true':'false');}
reg.pushManager.getSubscription().then(function(sub){show(!!sub);});
ab.onclick=function(){reg.pushManager.getSubscription().then(function(sub){
return fetch('/push/key',{credentials:'same-origin'}).then(function(r){return r.json();}).then(function(k){
if(sub){return post('/push/unsubscribe',{endpoint:sub.endpoint},k.csrf).then(function(){return sub.unsubscribe();})
.then(function(){show(false);note('');});}
if(!k.enabled){note('Alerts are not set up on the server.');return;}
return Notification.requestPermission().then(function(p){
if(p!=='granted'){note('Notifications are blocked for this site in the browser settings.');return;}
return reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:key(k.key)}).then(function(s){
return post('/push/subscribe',s.toJSON(),k.csrf).then(function(r){
if(!r.ok){s.unsubscribe();note('Could not save the subscription.');return;}show(true);note('');});});});
});}).catch(function(){note('Could not change alerts. Try again.');});};
}).catch(function(){});}
function focus(card){var s=document.querySelector('[data-stack]');if(!s)return;
var cards=s.querySelectorAll('[data-card]');
for(var i=0;i<cards.length;i++){cards[i].classList.remove('is-selected');cards[i].removeAttribute('aria-current');
cards[i].setAttribute('href',cards[i].getAttribute('data-next'));}
card.classList.add('is-selected');card.setAttribute('aria-current','true');
card.setAttribute('href',card.getAttribute('data-open'));
var label=card.getAttribute('data-label'),proj=card.getAttribute('data-project');
s.querySelector('[data-target]').textContent='\u2192 '+label;s.querySelector('[data-tname]').textContent=label;
var nf=s.querySelector('[data-nofocus]');if(nf)nf.hidden=!!proj;
var fm=s.querySelector('form.dbar');
if(fm){fm.elements.project.value=proj;fm.elements.next.value=card.getAttribute('data-next');}
try{history.replaceState(null,'',card.getAttribute('data-next'));}catch(e){}}
document.addEventListener('click',function(e){var a=e.target.closest?e.target.closest('[data-card]'):null;
if(!a||e.metaKey||e.ctrlKey||e.shiftKey||e.altKey)return;
e.preventDefault();if(a.classList.contains('is-selected'))sheetOpen(document.getElementById(a.getAttribute('data-detail')),a);
else focus(a);});
var flash=null,flashT;
function showFlash(){var e=document.querySelector('[data-flash]');if(!e||!flash)return;
e.textContent=flash.t;e.hidden=false;e.className='dbar-flash'+(flash.ok?'':' dbar-flash--err');}
function setFlash(t,ok){flash={t:t,ok:ok};showFlash();clearTimeout(flashT);
flashT=setTimeout(function(){flash=null;var e=document.querySelector('[data-flash]');if(e)e.hidden=true;},8000);}
function newRid(){var a=new Uint8Array(8),s='web-';crypto.getRandomValues(a);
for(var i=0;i<8;i++)s+=('0'+a[i].toString(16)).slice(-2);return s;}
function actFetch(fm,p,cb){fetch(fm.action,{method:'POST',credentials:'same-origin',headers:{'accept':'application/json'},body:p})
.then(function(r){return r.json().catch(function(){return {ok:false,message:'The desk answered '+r.status};});})
.catch(function(){return {ok:false,net:true,message:'No connection. Reload the desk.'};}).then(cb);}
function actSettle(fm,p,cb,n){actFetch(fm,p,function(j){
if(j.ok&&j.pending&&n<20){setTimeout(function(){actSettle(fm,p,cb,n+1);},1500);return;}cb(j);});}
function freshRid(fm){var r=fm.elements.rid;if(r)r.value=newRid();}
document.addEventListener('submit',function(e){var fm=e.target;
if(!fm.classList||!fm.classList.contains('dbar')||!window.fetch||!window.URLSearchParams)return;
e.preventDefault();if(fm.classList.contains('is-sending'))return;fm.classList.add('is-sending');
var go=fm.querySelector('.dbar__go'),was=go.textContent,txt=fm.elements.text.value,p=new URLSearchParams(new FormData(fm));
go.textContent='Sent ✓';fm.elements.text.value='';
actSettle(fm,p,function(j){fm.classList.remove('is-sending');
if(j.ok){freshRid(fm);setFlash(j.pending?'Sent. Still saving; it will arrive shortly.':(j.message||'Sent.'),true);
setTimeout(function(){go.textContent=was;},1800);return;}
if(!j.net)freshRid(fm);fm.elements.text.value=txt;go.textContent=was;setFlash(j.message||'Could not send.',false);},0);});
function rvPost(fm,extra,done){var p=new URLSearchParams(new FormData(fm));
for(var k in extra){if(Object.prototype.hasOwnProperty.call(extra,k))p.set(k,extra[k]);}
fm.classList.add('is-sending');
actSettle(fm,p,function(j){fm.classList.remove('is-sending');done(j);},0);}
function rvDisarm(fm){var b=fm.querySelector('button');clearTimeout(fm.rvT);
fm.removeAttribute('data-step');fm.removeAttribute('data-extra');b.classList.remove('is-armed');
b.textContent=b.getAttribute('data-label');}
document.addEventListener('submit',function(e){var fm=e.target;
if(!fm.hasAttribute||!fm.hasAttribute('data-rvclose')||!window.fetch||!window.URLSearchParams)return;
e.preventDefault();if(fm.classList.contains('is-sending'))return;
var b=fm.querySelector('button'),step=fm.getAttribute('data-step');
if(!step){rvPost(fm,{},function(j){
if(j.ok&&j.confirm){fm.setAttribute('data-step',j.step);fm.setAttribute('data-rid',j.rid);
fm.setAttribute('data-extra',JSON.stringify(j.extra||{}));b.classList.add('is-armed');b.textContent=j.label;
fm.rvT=setTimeout(function(){rvDisarm(fm);},6000);}
else if(j.ok){refreshMain();}
else{b.textContent='Failed';b.title=j.message||'';fm.rvT=setTimeout(function(){rvDisarm(fm);},3000);}});return;}
var extra={};try{extra=JSON.parse(fm.getAttribute('data-extra')||'{}');}catch(x){}
clearTimeout(fm.rvT);extra.step=step;extra.rid=fm.getAttribute('data-rid');
rvPost(fm,extra,function(j){rvDisarm(fm);
if(j.ok){var row=fm.closest('.rvrow');if(row&&!fm.classList.contains('rv__x--bulk')&&row.parentNode)row.parentNode.removeChild(row);refreshMain();}
else{b.textContent='Failed';b.title=j.message||'';fm.rvT=setTimeout(function(){rvDisarm(fm);},3000);}});});
var CONFIRMED={merge:1,control:1,wake:1,restart:1,watchdog:1,run:1,signin:1};
var sgMem='',sgHtml=null,sgT=0,sgE=0,sgOn=false;
function sgBox(){return document.getElementById('signin-flow');}
function sgTok(v){if(v!==undefined){sgMem=v;try{if(v)sessionStorage.setItem('hive-signin',v);else sessionStorage.removeItem('hive-signin');}catch(e){}return v;}
try{return sessionStorage.getItem('hive-signin')||sgMem;}catch(e){return sgMem;}}
function sgPaint(h){var s=sgBox();if(!s)return;sgHtml=h;s.innerHTML=h;stamps();clearTimeout(sgE);
var e=s.querySelector('[data-ends]');if(!e)return;
sgE=setTimeout(function(){sgPaint('<p class=mute role=status>This sign-in has ended.</p>');sgPoll();},
Math.max(0,+e.getAttribute('data-ends')-Date.now())+1500);}
function sgRestore(){if(sgHtml!==null)sgPaint(sgHtml);}
function sgGot(j,polled){var s=sgBox();if(!s)return;if(typeof j.flow==='string')sgTok(j.flow);
var i=s.querySelector('input[name=code]'),typing=polled&&j.active&&i&&(i.value||document.activeElement===i);
if(typeof j.html==='string'&&!typing&&j.html!==sgHtml)sgPaint(j.html);
clearTimeout(sgT);
if(j.active){sgT=setTimeout(sgPoll,2000);if(!sgOn){sgOn=true;refreshMain();}return;}
sgTok('');sgHtml=null;clearTimeout(sgE);if(sgOn||!polled){sgOn=false;refreshMain();}}
function sgPoll(){clearTimeout(sgT);var fm=document.getElementById('signin-poll');if(!fm||!sgBox())return;
var p=new URLSearchParams(new FormData(fm)),t=sgTok();if(t&&!p.get('flow'))p.set('flow',t);
actFetch(fm,p,function(j){if(j.ok)sgGot(j,true);else sgT=setTimeout(sgPoll,5000);});}
document.addEventListener('visibilitychange',function(){if(!document.hidden&&sgOn)sgPoll();});
function actName(fm){return (fm.getAttribute('action')||'').replace('/act/','');}
function actWarn(fm,t){var w=fm.querySelector('.act-warn');if(!t){if(w)w.parentNode.removeChild(w);return;}
if(!w){w=document.createElement('p');w.className='act-warn';w.setAttribute('role','alert');fm.appendChild(w);}w.textContent=t;}
function actDisarm(fm,b){clearTimeout(fm.acT);actWarn(fm,'');fm.removeAttribute('data-step');fm.removeAttribute('data-extra');
b.classList.remove('is-armed');b.textContent=b.getAttribute('data-was')||b.textContent;}
document.addEventListener('submit',function(e){var fm=e.target;
if(e.defaultPrevented||!fm.getAttribute||!window.fetch||!window.URLSearchParams)return;
var act=actName(fm),ok=(fm.getAttribute('action')||'').indexOf('/act/')===0&&act!=='chat'&&!fm.hasAttribute('data-rvclose');
if(!ok||fm.classList.contains('dbar'))return;
e.preventDefault();if(fm.classList.contains('is-sending')||fm.classList.contains('is-sent'))return;
var b=e.submitter||fm.querySelector('button');if(!b)return;
var p=new URLSearchParams(new FormData(fm));if(b.name)p.set(b.name,b.value);
var step=fm.getAttribute('data-step'),sig=b.name+'='+b.value;
if(step&&fm.acBtn!==sig){actDisarm(fm,fm.acEl||b);step=null;}
if(!b.getAttribute('data-was'))b.setAttribute('data-was',b.textContent);
if(step){clearTimeout(fm.acT);actWarn(fm,'');b.classList.remove('is-armed');p.set('step',step);var x={};try{x=JSON.parse(fm.getAttribute('data-extra')||'{}');}catch(_){}
for(var k in x){if(Object.prototype.hasOwnProperty.call(x,k))p.set(k,x[k]);}
p.set('rid',fm.getAttribute('data-rid')||p.get('rid'));}
var two=CONFIRMED[act]&&!step;
fm.classList.add('is-sending');
if(!two){b.textContent=act==='run'?'Running…':'Sent ✓';b.classList.add('is-sent');}
actSettle(fm,p,function(j){fm.classList.remove('is-sending');
if(j.ok&&j.confirm){b.classList.remove('is-sent');fm.setAttribute('data-step',j.step);fm.setAttribute('data-rid',j.rid);
fm.setAttribute('data-extra',JSON.stringify(j.extra||{}));fm.acBtn=sig;fm.acEl=b;b.classList.add('is-armed');b.textContent=j.label||'Tap again';actWarn(fm,j.detail);
fm.acT=setTimeout(function(){actDisarm(fm,b);},j.detail?(j.detail.length>200?30000:15000):8000);return;}
if(act==='run'){runShow(fm,b,j);return;}
if(j.ok&&act.indexOf('signin')===0){actDisarm(fm,b);b.classList.remove('is-sent');
var sc=fm.querySelector('input[type=text]');if(sc)sc.value='';sgGot(j,false);return;}
if(j.ok){fm.classList.add('is-sent');b.textContent=j.pending?'Sent ✓ saving…':'Sent ✓';
b.classList.remove('is-armed');b.classList.add('is-sent');
var t=fm.querySelector('textarea,input[type=text]');if(t)t.value='';
if(act==='ticket')setTimeout(function(){fm.classList.remove('is-sent');b.classList.remove('is-sent');b.textContent=b.getAttribute('data-was');freshRid(fm);},2500);return;}
b.classList.remove('is-sent','is-armed');b.textContent='Failed ✗';b.title=j.message||'';
var m=fm.querySelector('.act-err');if(!m){m=document.createElement('p');m.className='act-err';m.setAttribute('role','alert');fm.appendChild(m);}
m.textContent=j.message||'Could not send.';
fm.removeAttribute('data-step');fm.removeAttribute('data-extra');if(!j.net)freshRid(fm);
setTimeout(function(){b.textContent=b.getAttribute('data-was');},3000);},0);});
function runShow(fm,b,j){fm.classList.add('is-shown');b.classList.remove('is-armed','is-sent');
b.textContent=!j.ok?'Failed ✗':j.pending?'Running…':'Done ✓';
var m=fm.querySelector('.act-ok,.act-err');if(m)m.parentNode.removeChild(m);
m=document.createElement('p');m.className=j.ok?'act-ok':'act-err';m.setAttribute('role',j.ok?'status':'alert');
m.textContent=j.ok&&j.pending?'Still running. The list of runs shows the result when it finishes.':(j.message||(j.ok?'Done.':'Could not run it.'));
fm.appendChild(m);fm.removeAttribute('data-step');fm.removeAttribute('data-extra');
if(j.reload)setTimeout(function(){location.reload();},j.reload*1000);
setTimeout(function(){fm.classList.remove('is-shown');b.textContent=b.getAttribute('data-was');
if(m.parentNode)m.parentNode.removeChild(m);fm.reset();},9000);}
document.addEventListener('change',function(e){var fm=e.target&&e.target.form;
if(fm&&fm.getAttribute('data-step'))actDisarm(fm,fm.acEl||fm.querySelector('button'));});
var cf=document.querySelector('form.chatf');
function lmeta(m,t,err){var b=th&&atBottom(),s=m.el.querySelector('.meta');s.textContent=t;m.el.classList.toggle('is-err',!!err);
if(err){var r=document.createElement('button');r.type='button';r.className='quiet';r.textContent='Retry';
r.onclick=function(){send(m,0);};s.appendChild(r);}if(b)bottom();}
function send(m,n){if(locals.indexOf(m)<0)return;lmeta(m,'Sending\u2026');
fetch(cf.action,{method:'POST',credentials:'same-origin',headers:{'accept':'application/json'},
body:new URLSearchParams({text:m.text,rid:m.rid,csrf:cf.elements.csrf.value,next:'/chat'})})
.then(function(r){if((r.headers.get('content-type')||'').indexOf('application/json')<0)throw r.status;
return r.json();}).then(function(j){if(locals.indexOf(m)<0)return;
if(!j.ok){lmeta(m,'Not sent: '+(j.message||'try again'),true);return;}
if(j.pending){if(n<4)setTimeout(function(){send(m,n+1);},3000);
else lmeta(m,'Not confirmed. It may still arrive.',true);refreshThread();return;}
lmeta(m,'Sent');refreshThread();})
.catch(function(s){lmeta(m,s===403?'Not sent: this page has expired. Reload it.':
typeof s==='number'?'Not sent: the desk answered '+s:'Not sent: no connection',true);});}
function chatSend(){var ta=cf.elements.text,text=ta.value.trim();if(!text)return;
var rid=cf.elements.rid.value,el=document.createElement('div');
el.className='msg me local';el.innerHTML='<div class=bubble></div><span class=meta></span>';
el.firstChild.textContent=text;var m={el:el,text:text,rid:rid};locals.push(m);
var e=th.querySelector('p.empty');if(e)th.removeChild(e);
th.appendChild(el);bottom();jump(false);ta.value='';cf.elements.rid.value=newRid();ta.focus();send(m,0);}
if(cf&&th&&window.fetch&&window.URLSearchParams){
cf.addEventListener('submit',function(e){e.preventDefault();chatSend();});
cf.elements.text.addEventListener('keydown',function(e){
if(e.key!=='Enter'||e.shiftKey||e.isComposing||e.keyCode===229)return;e.preventDefault();chatSend();});}
var picks={},lastPick=null;
function calm(){return !(window.matchMedia&&matchMedia('(prefers-reduced-motion: no-preference)').matches);}
function groups(){return document.querySelectorAll('[data-group]');}
function itemKey(it){return it.getAttribute('data-open')+' '+it.getAttribute('data-item');}
function sync(g){var cur=g.querySelector('.nyx.is-primary'),a=g.querySelectorAll('.nyx__head');
for(var i=0;i<a.length;i++){var on=a[i].closest('.nyx')===cur;a[i].setAttribute('role','button');
a[i].setAttribute('aria-expanded',on?'true':'false');}}
function describe(it,n){var p=it.querySelector('.nyx__desc'),u=it.getAttribute('data-desc');
if(!p||!u||!it.classList.contains('is-primary')||p.getAttribute('data-ready')||p.busy)return;
p.busy=true;if(!p.textContent){p.hidden=false;p.className='nyx__desc is-pending';p.textContent='Reading the backlog item\u2026';}
fetch(u,{credentials:'same-origin',cache:'no-store'}).then(function(r){return r.ok?r.json():{};}).then(function(j){
p.busy=false;if(j.state==='ready'&&j.text){p.className='nyx__desc';p.textContent=j.text;p.setAttribute('data-ready','1');
p.setAttribute('tabindex','0');p.setAttribute('role','button');p.setAttribute('aria-haspopup','dialog');p.hidden=false;}
else if(j.state==='pending'&&(n||0)<20)setTimeout(function(){describe(it,(n||0)+1);},2000);
else{p.hidden=true;p.textContent='';}}).catch(function(){p.busy=false;p.hidden=true;p.textContent='';});}
function describeAll(){var a=document.querySelectorAll('.nyx.is-primary');for(var i=0;i<a.length;i++)describe(a[i]);}
function pick(g,it,animate){var cur=g.querySelector('.nyx.is-primary');if(cur===it)return;
var list=[].slice.call(g.querySelectorAll('.nyx')),y=list.map(function(r){return r.getBoundingClientRect().top;});
var act=document.activeElement,keep=act&&it.contains(act)?act:null;
if(cur){var ph=document.createComment('');it.parentNode.insertBefore(ph,it);cur.parentNode.insertBefore(it,cur);
ph.parentNode.insertBefore(cur,ph);ph.parentNode.removeChild(ph);cur.classList.remove('is-primary');}
else it.parentNode.insertBefore(it,it.parentNode.firstChild);
it.classList.add('is-primary');sync(g);if(keep)keep.focus({preventScroll:true});
if(!animate)return;
it.classList.add('is-lifted','is-opening');
list.forEach(function(r,i){var d=y[i]-r.getBoundingClientRect().top;
if(d){r.style.transition='none';r.style.transform='translateY('+d+'px)';}});
void g.offsetHeight;
list.forEach(function(r){if(r.style.transform){r.classList.add('is-flip');r.style.transition='';r.style.transform='';}});
setTimeout(function(){list.forEach(function(r){r.classList.remove('is-flip','is-lifted','is-opening');
r.style.transition='';r.style.transform='';});},340);}
function restore(){var g=groups();for(var i=0;i<g.length;i++){var k=g[i].getAttribute('data-group'),id=picks[k];
if(id!==undefined){var its=g[i].querySelectorAll('.nyx'),hit=null;
for(var j=0;j<its.length;j++)if(itemKey(its[j])===id)hit=its[j];
if(hit)pick(g[i],hit,false);else delete picks[k];}sync(g[i]);}if(tabsel)tabShow(tabsel);describeAll();barRoom();sgRestore();}
var tabsel=null;
function tabShow(k){[].forEach.call(document.querySelectorAll('[data-tabset]'),function(s){var on=s.querySelector('[data-tab="'+k+'"]');
if(!on)return;[].forEach.call(s.querySelectorAll('[data-tab]'),function(b){b.setAttribute('aria-selected',b===on?'true':'false');});
var r=s.getAttribute('data-tabset')==='r',at=r?'data-rp':'data-tp',pg=s.closest('.pgb')||document;
[].forEach.call(pg.querySelectorAll('['+at+']'),function(p){p.classList.toggle(r?'is-ron':'is-on',p.getAttribute(at)===k);});});}
document.addEventListener('click',function(e){var b=e.target.closest?e.target.closest('[data-tab]'):null;if(!b)return;
e.preventDefault();tabsel=b.getAttribute('data-tab');tabShow(tabsel);});
document.addEventListener('click',function(e){var o=e.target.closest?e.target.closest('.opt[data-pick]'):null;if(!o)return;
var all=o.parentNode.querySelectorAll('.opt[data-pick]'),ls='',L=o.getAttribute('data-pick');
for(var i=0;i<all.length;i++){all[i].setAttribute('aria-pressed',all[i]===o?'true':'false');ls+=all[i].getAttribute('data-pick');}
var c=o.closest('.nyx,.need'),t=c&&c.querySelector('textarea[name=text],input[name=text]');if(!t)return;
var m=new RegExp('^['+ls+'](?:$|[.] *)').exec(t.value),rest=m?t.value.slice(m[0].length):t.value;
t.value=L+(rest?'. '+rest:'');});
document.addEventListener('click',function(e){
var h=e.target.closest?e.target.closest('.nyx__head'):null;
if(!h||e.button||e.metaKey||e.ctrlKey||e.shiftKey||e.altKey)return;
var it=h.closest('.nyx'),g=it.closest('[data-group]'),k=g.getAttribute('data-group'),now=Date.now(),
pos=[].indexOf.call(g.querySelectorAll('.nyx'),it);
e.preventDefault();
if(lastPick&&lastPick.k===k&&now-lastPick.t<450&&g.querySelector('.nyx.is-primary')===lastPick.it&&
(it===lastPick.it||pos===lastPick.pos)){var o=lastPick.it.getAttribute('data-open');lastPick=null;location.href=o;return;}
lastPick={k:k,it:it,pos:pos,t:now};picks[k]=itemKey(it);
pick(g,it,!calm());describe(it);});
var sheetOpener=null;
function sheetOpen(d,b){if(!d||d.open)return;sheetOpener=b;
if(d.showModal)d.showModal();else d.setAttribute('open','');
var l=d.querySelector('.sheet__list');if(l)l.scrollTop=0;var x=d.querySelector('[data-sheet-close]');if(x)x.focus();}
function sheetClose(d){if(d.close)d.close();else d.removeAttribute('open');
if(sheetOpener&&document.contains(sheetOpener))sheetOpener.focus();sheetOpener=null;}
function descOpen(p){var d=document.getElementById('desc-sheet'),it=p.closest('.nyx');if(!d)return;
d.querySelector('[data-sheet-title]').textContent=it?it.getAttribute('data-item'):'';
d.querySelector('[data-sheet-text]').textContent=p.textContent;sheetOpen(d,p);}
document.addEventListener('click',function(e){var t=e.target;if(!t||!t.closest)return;
var o=t.closest('[data-sheet-open]');
if(o){if(e.metaKey||e.ctrlKey||e.shiftKey||e.altKey)return;
e.preventDefault();sheetOpen(document.getElementById(o.getAttribute('data-sheet-open')),o);return;}
var p=t.closest('.nyx__desc[data-ready]');if(p){e.preventDefault();descOpen(p);return;}
var d=t.closest('[data-sheet]');
if(t.closest('[data-sheet-close]')){e.preventDefault();sheetClose(d);return;}
if(t.hasAttribute&&t.hasAttribute('data-sheet'))sheetClose(t);});
document.addEventListener('click',function(e){var qc=document.getElementById('qchip');
if(qc&&qc.open&&!qc.contains(e.target))qc.open=false;});
document.addEventListener('cancel',function(e){var d=e.target;
if(d&&d.hasAttribute&&d.hasAttribute('data-sheet')){e.preventDefault();sheetClose(d);}},true);
document.addEventListener('keydown',function(e){var t=e.target;
if(e.key==='Escape'){var qc=document.getElementById('qchip');
if(qc&&qc.open){qc.open=false;var qs=qc.querySelector('summary');if(qs)qs.focus();return;}
var d=document.querySelector('[data-sheet][open]');if(d){e.preventDefault();sheetClose(d);}return;}
if((e.key==='Enter'||e.key===' ')&&t&&t.matches&&t.matches('.nyx__desc[data-ready]')){e.preventDefault();descOpen(t);}});
function barRoom(){try{var w=document.querySelector('.dbar-wrap,.dock');
document.documentElement.style.setProperty('--bar-room',(w?Math.ceil(w.getBoundingClientRect().height)+28:0)+'px');}catch(e){}}
try{barRoom();window.addEventListener('resize',barRoom);
if(window.ResizeObserver){var bw=document.querySelector('.dbar-wrap,.dock');if(bw)new ResizeObserver(barRoom).observe(bw);}}catch(e){}
if(!th&&document.body&&document.body.classList&&!document.body.classList.contains('termpage')&&window.visualViewport){var dv=window.visualViewport;
function dfit(){document.documentElement.style.setProperty('--app-h',dv.height+'px');window.scrollTo(0,0);}
dv.addEventListener('resize',dfit);dv.addEventListener('scroll',function(){window.scrollTo(0,0);});dfit();}
if(th&&window.visualViewport){var vv=window.visualViewport;
function fit(){var was=atBottom();document.documentElement.style.setProperty('--app-h',vv.height+'px');
var d=document.querySelector('.dock');if(d)document.documentElement.style.setProperty('--dock-h',d.offsetHeight+'px');
window.scrollTo(0,0);if(was)bottom();}
vv.addEventListener('resize',fit);vv.addEventListener('scroll',function(){window.scrollTo(0,0);});fit();}
if(sgBox()){sgOn=sgBox().hasAttribute('data-active');if(sgTok()||sgOn)sgPoll();}
restore();stamps();setInterval(stamps,30000);
})();
"""


SCRIPT_CSP_HASH = "sha256-" + base64.b64encode(hashlib.sha256(SCRIPT.encode()).digest()).decode()
