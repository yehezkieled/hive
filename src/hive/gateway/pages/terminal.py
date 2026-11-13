# ruff: noqa: E501  (inline CSS and JS)
"""The Terminal page: the "confirm it is you" gate and the live terminal it opens into."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from hive.gateway.pages.shell import Ctx, _page

# ---- Terminal page ---------------------------------------------------------------------------
# Firstmate's own herdr pane, behind the step-up (see ``terminal.py``). The page carries no
# terminal data: it shows a "confirm it is you" screen, and only a finished step-up's pass
# opens the websocket that streams the screen.

VENDOR_DIR = Path(__file__).resolve().parents[1] / "static" / "vendor"


XTERM_JS = "xterm-6.0.0.js"


XTERM_SRI = (
    "sha384-"
    + base64.b64encode(hashlib.sha384((VENDOR_DIR / XTERM_JS).read_bytes()).digest()).decode()
)


XTERM_CSS = (VENDOR_DIR / "xterm-6.0.0.css").read_text()


TERMINAL_CSS = """
body.termpage{position:fixed;top:0;left:0;width:100%}
.termpage main{overflow:hidden}
.term{display:flex;flex-direction:column;gap:10px}
.tbar{flex:none;display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px;min-height:60px;
padding:8px 10px 8px 14px;border:1px solid var(--line2);border-radius:var(--r3);background:var(--card)}
.tbar[data-s=mine]{border-color:var(--down);box-shadow:0 0 0 3px var(--down-t)}
.tstate{flex:none;display:inline-flex;align-items:center;gap:6px;height:26px;padding:0 10px;border-radius:999px;
background:var(--unk-t);font:500 13px var(--ft);color:var(--ink);white-space:nowrap}
.tstate::before{content:"";flex:none;width:8px;height:9px;clip-path:var(--hex);background:var(--unk)}
.tstate--closed{background:none;box-shadow:inset 0 0 0 1px var(--line2)}.tstate--closed::before{background:var(--line2)}
.tstate--ro{background:var(--ok-t)}.tstate--ro::before{background:var(--ok)}
.tstate--mine,.tstate--down{background:var(--down-t)}.tstate--mine::before,.tstate--down::before{background:var(--down)}
.tstate--other{background:var(--warn-t)}.tstate--other::before{background:var(--warn)}
.tstate--wait{background:var(--run-t)}.tstate--wait::before{background:var(--run)}
.tbar__who{flex:1 1 240px;min-width:0;font-size:15px;line-height:1.35;overflow-wrap:anywhere}
.tbar__btns{flex:none;display:flex;gap:8px;margin-left:auto}.tbar__btns:empty{display:none}
.tbar button[hidden]{display:none}.tbar button:disabled{opacity:.45;cursor:not-allowed}
.term>.act-warn{flex:none;margin:0;padding:10px 14px;border-radius:var(--r2);background:var(--warn-t);color:var(--ink)}
.term .act-warn[hidden]{display:none}
.tgate{flex:1;min-height:0;overflow:auto;display:flex;flex-direction:column;align-items:center;padding:2px}
.tgate[hidden],.tlive[hidden]{display:none}
.gate{width:min(680px,100%);margin:auto 0;padding:28px;border:1px solid var(--line);border-radius:var(--r3);
background:var(--card);text-align:center}
.gate h2{font-size:26px;letter-spacing:-.02em}
.facts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:20px 0 22px;padding:0;list-style:none;
text-align:left}
.facts li{display:flex;align-items:center;gap:12px;min-height:58px;padding:10px 14px;border-radius:var(--r2);
background:var(--surface)}
.facts svg{flex:none;width:22px;height:22px;fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round;
stroke-linejoin:round}
.facts span{display:flex;flex-direction:column;gap:2px;min-width:0}
.facts b{font-size:16px;font-weight:600}
.facts small{font-size:13px;color:var(--muted)}
.gate form{display:flex;flex-direction:column;align-items:center;gap:8px;margin:0}
.gate .act-warn{margin:0;color:var(--ink);font-weight:500}
.tnote{flex:none;min-height:1.3em;margin:0;font:600 14px/1.4 var(--ft);color:var(--down)}
.tnote:empty{min-height:0}
.gate .tnote:not(:empty){margin-top:10px}
.tlive{flex:1;min-height:0;display:flex;flex-direction:column;gap:10px}
.twrap{position:relative;flex:1;min-height:140px;display:flex}
.tframe{flex:1;min-width:0;overflow:auto;-webkit-overflow-scrolling:touch;overscroll-behavior:contain;
padding:8px;background:#16140f;border:1px solid var(--line2);border-radius:var(--r3)}
.tframe .xterm{display:inline-block;vertical-align:top}
/* The desk's textarea min-height would stretch xterm's hidden input below the screen. */
.tframe .xterm-helper-textarea{min-height:0}
.tframe__msg{position:absolute;inset:1px;display:flex;align-items:center;justify-content:center;margin:0;
padding:24px;border-radius:calc(var(--r3) - 1px);background:#16140f;color:#e9dfc9;font:600 15px/1.5 var(--ft);
text-align:center}
.tframe__msg[hidden]{display:none}
.tjump{position:absolute;left:50%;bottom:14px;transform:translateX(-50%);display:inline-flex;align-items:center;
min-height:44px;padding:0 16px;border:1px solid var(--line2);border-radius:999px;background:var(--card);color:var(--ink);
font:600 14px var(--ft);box-shadow:0 4px 14px rgba(0,0,0,.35);cursor:pointer}
.tjump[hidden]{display:none}
.tkeys{flex:none;display:flex;gap:6px;overflow-x:auto;-webkit-overflow-scrolling:touch;scrollbar-width:none;padding:1px}
.tkeys::-webkit-scrollbar{display:none}
.tkeys button{flex:none;min-width:54px;height:48px;padding:0 12px;border:1px solid var(--line2);border-radius:var(--r2);
background:var(--card);color:var(--ink);font:500 15px var(--fm);cursor:pointer}
.tkeys button[aria-pressed=true]{background:var(--ink);border-color:var(--ink);color:var(--card)}
.tkeys button:disabled{opacity:.45;cursor:not-allowed}
.tkeys__gap{flex:1 0 12px}
.tpaste{flex:none;display:flex;gap:8px;align-items:flex-end;margin:0}
.tpaste textarea{flex:1;min-width:0;height:48px;min-height:48px;max-height:22vh;margin:0;resize:none;
font:16px/1.35 var(--fm)}
.tpaste textarea:disabled{opacity:.6}
.tpaste .btn{flex:none;min-height:48px}
.tpaste .btn:disabled{opacity:.45;cursor:not-allowed;transform:none}
@media (prefers-color-scheme:dark){.tframe,.tframe__msg{background:#0f0d09}}
/* A phone keeps every row it can for the screen: the live terminal drops the page title (the
menu marks the page) and the paste box keeps one row, so 30 rows fit at the smallest font. */
@media (max-width:640px){.termpage .ph .sub{display:none}.termpage:has(.tlive:not([hidden])) .ph{display:none}
.termpage:has(.tlive:not([hidden])) .term{padding-top:12px}.tbar{padding:8px}.tbar__who{flex-basis:100%;order:3}
.facts{grid-template-columns:minmax(0,1fr)}.facts li{min-height:48px}.gate{padding:18px}.gate h2{font-size:22px}
.tpaste .btn{padding:0 12px}}
/* every key in one row on an iPad in portrait too (a phone's row scrolls sideways) */
@media (max-width:1023px){.tkeys{gap:4px}.tkeys button{min-width:46px;padding:0 8px}}
"""


# Pinned by hash in the Terminal page's CSP, as ``SCRIPT`` is everywhere. It takes no server
# data: every word it shows comes from the websocket's messages, set as text.
TERMINAL_SCRIPT = r"""
(function(){
var $=function(i){return document.getElementById(i);};
var form=$('term-open');if(!form)return;
var gate=$('term-gate'),live=$('term-live'),gnote=$('term-gate-note'),bar=$('term-bar'),stEl=$('term-state'),
whoEl=$('term-who'),takeB=$('term-take'),relB=$('term-release'),warnEl=$('term-warn'),frame=$('term-frame'),
scrEl=$('term-screen'),msgEl=$('term-msg'),jumpB=$('term-jump'),keysEl=$('term-keys'),ctrlB=$('term-ctrl'),
pasteT=$('term-paste'),sendB=$('term-send'),sendE=$('term-send-enter'),noteEl=$('term-note'),
smallB=$('term-smaller'),bigB=$('term-bigger'),fitB=$('term-fit'),openB=form.querySelector('button');
var openLabel=openB.textContent,takeLabel=takeB.textContent;
var KEYS={esc:'\x1b',tab:'\t',enter:'\r',left:'\x1b[D',up:'\x1b[A',down:'\x1b[B',right:'\x1b[C',
pgup:'\x1b[5~',pgdn:'\x1b[6~'};
var THEME={background:'#16140f',foreground:'#efe9da',cursor:'#e0a050',cursorAccent:'#16140f',
selectionBackground:'rgba(224,160,80,0.35)',black:'#2a261d',red:'#f0715f',green:'#7fc48c',yellow:'#e8b640',
blue:'#7aa7d9',magenta:'#d29be0',cyan:'#6cc4c0',white:'#d6cfbf',brightBlack:'#7c7566',brightRed:'#ff8f7f',
brightGreen:'#9adba5',brightYellow:'#f5cd6a',brightBlue:'#9cc2ee',brightMagenta:'#e4b6ef',brightCyan:'#8fdcd8',
brightWhite:'#faf7ed',scrollbarSliderBackground:'rgba(239,233,218,0.18)',
scrollbarSliderHoverBackground:'rgba(239,233,218,0.32)',scrollbarSliderActiveBackground:'rgba(239,233,218,0.45)'};
// The system monospace, not the desk's IBM Plex Mono: Plex has no box-drawing glyphs, and a
// fallback glyph of another width would break Claude Code's boxes. SLACK spare columns keep
// the scrollbar off the last column.
var FONT='Menlo, "SF Mono", ui-monospace, "DejaVu Sans Mono", "Liberation Mono", Consolas, monospace',SLACK=3;
if(window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches){THEME.background='#0f0d09';THEME.cursorAccent='#0f0d09';}
var pass=null,ends=0,ws=null,tries=0,lastEnd='',st=null,mine=false,ctrl=false,takeStep=null,takeT=0,armed=null,
armT=0,armRid='',joined=false,term=null,cols=0,rows=0,screen=[],histAt=0,wasUp=false,needHist=true,fs=13,fit=true,idleEnds=0,noteT=0,
lastW=0,lastH=0,rt=0;
try{var pr=JSON.parse(localStorage.getItem('hive-terminal')||'{}');
if(pr.fs>=6&&pr.fs<=28)fs=pr.fs;if(pr.fit===false)fit=false;}catch(e){}
function save(){try{localStorage.setItem('hive-terminal',JSON.stringify({fs:fs,fit:fit}));}catch(e){}}
function hm(ms){try{return new Date(ms).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'});}catch(e){return '';}}
function note(t){noteEl.textContent=t||'';clearTimeout(noteT);if(t)noteT=setTimeout(function(){noteEl.textContent='';},8000);}
function warnIn(el,t){el.textContent=t||'';el.hidden=!t;}
function fwarn(t){var w=form.querySelector('.act-warn');if(!w){w=document.createElement('p');w.className='act-warn';
w.setAttribute('role','alert');form.appendChild(w);}warnIn(w,t);}
function disarm(){clearTimeout(armT);armed=null;openB.classList.remove('is-armed');openB.textContent=openLabel;fwarn('');}
function disarmTake(){clearTimeout(takeT);takeStep=null;takeB.classList.remove('is-armed');takeB.textContent=takeLabel;
warnIn(warnEl,'');}
function send(o){if(ws&&ws.readyState===1){ws.send(JSON.stringify(o));return true;}return false;}
function setCtrl(on){ctrl=!!on;ctrlB.setAttribute('aria-pressed',ctrl?'true':'false');}
function overlay(t){msgEl.textContent=t||'';msgEl.hidden=!t;}
function readOnly(on){var b=keysEl.querySelectorAll('button[data-k]');for(var i=0;i<b.length;i++)b[i].disabled=on;
ctrlB.disabled=on;pasteT.disabled=on;sendB.disabled=on;sendE.disabled=on;
pasteT.placeholder=on?'Read-only':'Text to send';if(on)setCtrl(false);
if(!term)return;term.options.disableStdin=on;var ta=term.textarea;if(!ta)return;
if(on){ta.setAttribute('inputmode','none');ta.readOnly=true;if(document.activeElement===ta)ta.blur();}
else{ta.removeAttribute('inputmode');ta.readOnly=false;}}
function show(phase){var s='closed',label='Not open',who='Confirm it is you to open firstmate’s session.';
if(phase==='connecting'||phase==='reconnecting'){s='wait';label=phase==='connecting'?'Connecting':'Reconnecting';
who='Read-only while it connects. Nothing can be typed.';}
else if(st&&ws){var tabs=st.viewers===1?'1 tab':st.viewers+' tabs',
base='Attached: '+st.who+' · '+tabs+' · open until '+hm(st.ends*1000);
if(st.session==='missing'){s='down';label='Session not found';who=base;}
else if(st.session==='looking'){s='wait';label='Looking';who=base;}
else if(st.keyboard==='you'){s='mine';label='Keyboard taken';
who=base+' · read-only again at '+hm(idleEnds)+' if idle';}
else if(st.keyboard==='other'){s='other';label='Read-only';who='Another tab has the keyboard. '+base;}
else{s='ro';label='Read-only';who=base;}}
bar.setAttribute('data-s',s);stEl.className='tstate tstate--'+s;stEl.textContent=label;whoEl.textContent=who;
var on=!!(st&&ws&&st.session==='ok');takeB.hidden=!on||st.keyboard==='you';takeB.disabled=!on||st.keyboard==='other';
relB.hidden=!(on&&st.keyboard==='you');}
function size(c,r){c=Math.max(20,Math.min(1000,c|0));r=Math.max(1,Math.min(400,r|0));if(c===cols&&r===rows)return;
if(r!==rows)needHist=true;cols=c;rows=r;term.resize(c+SLACK,r);refit(true);toBottom();}
function rowsOut(lines){var s='';for(var i=0;i<rows;i++)s+='\x1b['+(i+1)+';1H\x1b[0m'+(lines[i]||'')+'\x1b[0m\x1b[K';return s;}
function paint(m){if(!term||!Array.isArray(m.lines))return;size(m.cols,m.rows);screen=m.lines;term.write(rowsOut(screen));
if(needHist&&st&&st.session==='ok')askHist();}
function askHist(){if(send({t:'history'})){histAt=Date.now();needHist=false;}}
function rebuild(m){if(!term||!Array.isArray(m.lines)||!Array.isArray(m.screen))return;
var b=term.buffer.active,back=b.baseY-b.viewportY;size(m.cols,m.rows);needHist=false;screen=m.screen;term.reset();
var s='\x1b[?7l\x1b[?25l';if(m.lines.length)s+=m.lines.join('\x1b[0m\r\n')+'\x1b[0m\r\n';
s+=new Array(rows).join('\r\n')+rowsOut(screen);
term.write(s,function(){if(back>0){term.scrollToLine(Math.max(0,term.buffer.active.baseY-back));}scrolled(true);});}
function scrolled(quiet){if(!term)return;var b=term.buffer.active,up=b.viewportY<b.baseY;
if(up&&!wasUp&&!quiet&&Date.now()-histAt>3000)askHist();wasUp=up;jumpB.hidden=!up;}
function typed(){idleEnds=Date.now()+((st&&st.idle_s)||600)*1000;show();}
function onData(d){if(!mine)return;if(ctrl&&d.length===1){var c=d.toLowerCase().charCodeAt(0);
if(c>=97&&c<=122)d=String.fromCharCode(c-96);setCtrl(false);}if(send({t:'in',d:d}))typed();}
function measure(){var el=term&&term.element&&term.element.querySelector('.xterm-screen');
if(!el)return null;var r=el.getBoundingClientRect(),f=term.options.fontSize;
return r.width&&r.height?{w:r.width/term.cols/f,h:r.height/term.rows/f}:null;}
function label(){var f=term?term.options.fontSize:fs;fitB.setAttribute('aria-pressed',fit?'true':'false');
fitB.title='Fit the screen ('+f+' px now)';}
// xterm 6 re-sizes its scrollbar on a scroll or a resize, not on a font change: a stale, taller
// bar would overflow the frame and toBottom() would scroll the pane's top rows out of view.
// The pinned build's viewport sync is called straight away (its queued sync can be dropped).
function font(n){if(term.options.fontSize===n)return;term.options.fontSize=n;
var v=term._core&&term._core._viewport;if(v&&v._sync)v._sync();}
function refit(force){if(!term)return;if(!fit){font(fs);label();return;}
var w=frame.clientWidth-16,h=frame.clientHeight-16;if(w<=0||h<=0)return;
if(!force&&w===lastW&&h<=lastH)return;lastW=w;lastH=h;var k=measure();if(!k)return;
var n=Math.floor(Math.min(w/(term.cols*k.w),h/(term.rows*k.h)));n=Math.max(7,Math.min(18,n));
font(n);while(n>7&&over(w,h)){n--;font(n);}label();toBottom();}
// xterm rounds a cell to whole pixels, so a cell does not grow in step with the font and the
// guess above can come out a row too tall, scrolling the top row out of view: step down to fit.
function over(w,h){var el=term.element&&term.element.querySelector('.xterm-screen');
if(!el)return false;var r=el.getBoundingClientRect();return r.width>w+0.5||r.height>h+0.5;}
function toBottom(){requestAnimationFrame(function(){frame.scrollTop=frame.scrollHeight;frame.scrollLeft=0;});}
function setFs(n){fit=false;fs=Math.max(6,Math.min(28,n));if(term)font(fs);save();label();toBottom();}
function vh(){var v=window.visualViewport;
document.documentElement.style.setProperty('--app-h',(v?v.height:window.innerHeight)+'px');window.scrollTo(0,0);}
function resized(){clearTimeout(rt);rt=setTimeout(function(){vh();refit(false);toBottom();},120);}
function ensureTerm(){if(term)return true;if(!window.Terminal)return false;
term=new window.Terminal({cols:80,rows:24,fontSize:fs,fontFamily:FONT,
theme:THEME,scrollback:3000,disableStdin:true,cursorBlink:false,cursorStyle:'bar',cursorInactiveStyle:'none',
convertEol:false,smoothScrollDuration:0,drawBoldTextInBrightColors:false,minimumContrastRatio:1});
cols=80;rows=24;term.open(scrEl);if(term.textarea)term.textarea.name='term-keys';term.write('\x1b[?7l\x1b[?25l');term.onData(onData);
term.onScroll(function(){scrolled(false);});
term.element.addEventListener('wheel',function(){setTimeout(function(){scrolled(false);},50);},{passive:true});
term.element.addEventListener('touchend',function(){setTimeout(function(){scrolled(false);},200);},{passive:true});
term.element.addEventListener('paste',function(e){e.preventDefault();e.stopPropagation();
var t=(e.clipboardData&&e.clipboardData.getData('text'))||'';if(mine&&t){pasteT.value=t;pasteT.focus();
note('Your paste is in the box below. Check it, then tap Send.');}},true);
readOnly(true);label();return true;}
function toGate(t){if(ws){var w=ws;ws=null;try{w.close();}catch(e){}}pass=null;st=null;mine=false;disarmTake();
if(term){readOnly(true);term.reset();term.write('\x1b[?7l\x1b[?25l');}screen=[];needHist=true;overlay('');
live.hidden=true;gate.hidden=false;gnote.textContent=t||'';show();}
function on(m){if(m.t==='state'){var was=mine;st=m;tries=0;joined=true;mine=m.keyboard==='you';
if(mine&&m.idle_ends)idleEnds=m.idle_ends*1000;if(mine!==was||!mine)readOnly(!mine);
if(m.keyboard!=='none')disarmTake();
if(m.session==='missing'){overlay((m.why||'Firstmate’s session was not found.')+' It shows here again once it is back.');screen=[];needHist=true;
term.reset();term.write('\x1b[?7l\x1b[?25l');}
else if(m.session==='looking')overlay('Looking for firstmate’s session…');
else{overlay('');if(needHist)askHist();}show();return;}
if(m.t==='screen'){paint(m);return;}
if(m.t==='history'){rebuild(m);return;}
if(m.t==='confirm'&&typeof m.step==='string'){takeStep=m.step;takeB.classList.add('is-armed');
takeB.textContent=m.label||'Tap again';warnIn(warnEl,m.detail||'');clearTimeout(takeT);takeT=setTimeout(disarmTake,50000);return;}
if(m.t==='notice'){note(m.text||'');return;}
if(m.t==='end'){lastEnd=m.text||'';}}
function connect(){if(!pass||Date.now()>=ends){toGate(lastEnd||'The terminal was open for its time limit. Confirm it is you to open it again.');return;}
var me;try{me=new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+'/terminal/ws');}
catch(e){toGate('The terminal could not connect to the desk.');return;}
ws=me;show(tries?'reconnecting':'connecting');
me.onopen=function(){if(ws===me)me.send(JSON.stringify({t:'attach',pass:pass}));};
me.onmessage=function(ev){if(ws!==me)return;var m;try{m=JSON.parse(ev.data);}catch(e){return;}
if(m&&typeof m.t==='string')on(m);};
me.onclose=function(ev){if(ws!==me)return;ws=null;st=null;mine=false;readOnly(true);disarmTake();var c=ev.code;
if(c===4401||c===4409||c===4429||c===4503||c===1001||c===1008){toGate(lastEnd||'The terminal closed. Confirm it is you to open it again.');return;}
if(!joined){toGate(lastEnd||'The terminal could not connect to the desk. Nothing was opened.');return;}
if(!pass||Date.now()>=ends||tries>=8){toGate(lastEnd||'The connection to the desk was lost. Confirm it is you to open it again.');return;}
tries++;show('reconnecting');setTimeout(connect,Math.min(15000,400*Math.pow(2,tries)));};}
function start(){gnote.textContent='';if(!ensureTerm()){gnote.textContent='The terminal could not load. Reload the page.';return;}
gate.hidden=true;live.hidden=false;overlay('Connecting…');lastW=0;vh();connect();}
form.addEventListener('submit',function(e){e.preventDefault();if(form.classList.contains('is-sending'))return;
if(!window.WebSocket||!window.fetch){gnote.textContent='This browser cannot open the terminal.';return;}
var p=new URLSearchParams(new FormData(form));if(armed){p.set('step',armed);p.set('rid',armRid);}
form.classList.add('is-sending');gnote.textContent='';
fetch(form.action,{method:'POST',credentials:'same-origin',headers:{'accept':'application/json'},body:p})
.then(function(r){if((r.headers.get('content-type')||'').indexOf('application/json')<0)throw r.status;return r.json();})
.then(function(j){form.classList.remove('is-sending');
if(j.ok&&j.confirm&&typeof j.step==='string'){armed=j.step;armRid=String(j.rid||'');openB.classList.add('is-armed');openB.textContent=j.label||'Tap again';
fwarn(j.detail||'');clearTimeout(armT);armT=setTimeout(disarm,20000);return;}
disarm();if(j.ok&&typeof j.pass==='string'){pass=j.pass;ends=j.ends*1000;tries=0;joined=false;lastEnd='';start();return;}
gnote.textContent=j.message||'The terminal did not open.';})
.catch(function(s){form.classList.remove('is-sending');disarm();
gnote.textContent=s===403?'This page has expired. Reload it, then try again.':
typeof s==='number'?'The desk answered '+s+'. The terminal did not open.':'No connection to the desk.';});});
takeB.addEventListener('click',function(){if(takeStep){var s=takeStep;disarmTake();send({t:'take',step:s});}
else send({t:'take'});});
relB.addEventListener('click',function(){send({t:'release'});});
keysEl.addEventListener('mousedown',function(e){if(e.target.closest('button'))e.preventDefault();});
keysEl.addEventListener('click',function(e){var b=e.target.closest('button[data-k]');if(!b||b.disabled||!mine)return;
var k=KEYS[b.getAttribute('data-k')];setCtrl(false);if(k&&send({t:'in',d:k}))typed();});
ctrlB.addEventListener('click',function(){if(mine)setCtrl(!ctrl);});
function sendPaste(enter){if(!mine)return;var t=pasteT.value;if(!t&&!enter)return;
if(t.length>4096){note('That is more than 4096 characters. Send it in parts.');return;}
if(send({t:'paste',d:t,enter:!!enter})){pasteT.value='';typed();}}
sendB.addEventListener('click',function(){sendPaste(false);});
sendE.addEventListener('click',function(){sendPaste(true);});
pasteT.addEventListener('keydown',function(e){if(e.key==='Enter'&&(e.metaKey||e.ctrlKey)){e.preventDefault();sendPaste(true);}});
smallB.addEventListener('click',function(){setFs((term?term.options.fontSize:fs)-1);});
bigB.addEventListener('click',function(){setFs((term?term.options.fontSize:fs)+1);});
fitB.addEventListener('click',function(){fit=true;save();refit(true);});
jumpB.addEventListener('click',function(){if(term){term.scrollToBottom();scrolled(true);}});
window.addEventListener('resize',resized);
window.addEventListener('orientationchange',function(){lastW=0;resized();});
if(window.visualViewport)window.visualViewport.addEventListener('resize',resized);
setInterval(function(){if(ws&&st)show();},15000);
window.addEventListener('pagehide',function(){if(ws){var w=ws;ws=null;try{w.close();}catch(e){}}});
vh();show();label();
})();
"""


TERMINAL_SCRIPT_CSP_HASH = (
    "sha256-" + base64.b64encode(hashlib.sha256(TERMINAL_SCRIPT.encode()).digest()).decode()
)


_KEY_ROW = (
    ("esc", "Esc", ""),
    ("ctrl", "Ctrl", ""),
    ("tab", "Tab", ""),
    ("left", "←", "Left"),
    ("up", "↑", "Up"),
    ("down", "↓", "Down"),
    ("right", "→", "Right"),
    ("pgup", "PgUp", "Page up"),
    ("pgdn", "PgDn", "Page down"),
    ("enter", "Enter", ""),
)


_TICON = {  # the gate's four facts, drawn as the board draws them
    "eye": "<svg viewBox='0 0 24 24' aria-hidden=true><path d='M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 "
    "12 2 12z'/><circle cx=12 cy=12 r=3 /></svg>",
    "key": "<svg viewBox='0 0 24 24' aria-hidden=true><rect x=2 y=6 width=20 height=12 rx=2 />"
    "<path d='M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10'/></svg>",
    "clock": "<svg viewBox='0 0 24 24' aria-hidden=true><circle cx=12 cy=12 r=9 />"
    "<path d='M12 7v5l3 2'/></svg>",
    "lock": "<svg viewBox='0 0 24 24' aria-hidden=true><rect x=4 y=11 width=16 height=10 rx=2 />"
    "<path d='M8 11V7a4 4 0 0 1 8 0v4'/></svg>",
}


def render_terminal(ctx: Ctx, enabled: bool) -> str:
    """The Terminal page: a "confirm it is you" screen, and the live terminal it opens into.

    Nothing from firstmate's session is in this page: the screen only ever arrives over the
    websocket, after a finished step-up.
    """
    head = (
        "<div class=ph><h1>Terminal</h1>"
        "<span class=sub>Firstmate’s own session, live, as it shows on the PC.</span></div>"
    )
    if not enabled:
        return _page(
            "Terminal · Hive desk",
            f"{head}<div class='pgb term' id=term><div class=banner role=status>The Terminal page is "
            "switched off on this desk (<code>HIVE_GATEWAY_TERMINAL=off</code>).</div></div>",
            "terminal",
            "",
            ctx,
        )
    keys = "".join(
        f"<button type=button id=term-ctrl aria-pressed=false disabled>{label}</button>"
        if key == "ctrl"
        else f"<button type=button data-k={key} disabled"
        + (f" aria-label='{aria}'" if aria else "")
        + f">{label}</button>"
        for key, label, aria in _KEY_ROW
    )
    form = ctx.form(
        "terminal",
        "<button class='btn btn--p' type=submit>Open terminal</button>",
        always=True,
    ).replace("<form ", "<form id=term-open ", 1)
    body = (
        f"{head}<div class='pgb term' id=term>"
        "<div class=tbar id=term-bar data-s=closed role=status aria-live=polite>"
        "<span class='tstate tstate--closed' id=term-state>Not open</span>"
        "<span class=tbar__who id=term-who>Confirm it is you to open firstmate’s session."
        "</span><span class=tbar__btns>"
        "<button type=button class='btn btn--s' id=term-take hidden>Take keyboard</button>"
        "<button type=button class='btn btn--s' id=term-release hidden>Release keyboard</button>"
        "</span></div><p class=act-warn id=term-warn role=alert hidden></p>"
        "<section class=tgate id=term-gate aria-label='Confirm it is you'><div class=gate>"
        "<h2>Confirm it is you</h2><ul class=facts>"
        f"<li>{_TICON['eye']}<span><b>Opens read-only</b>"
        "<small>Typing needs a second confirm: Take keyboard.</small></span></li>"
        f"<li>{_TICON['key']}<span><b>One tab types at a time</b>"
        "<small>Read-only again after 10 minutes without typing.</small></span></li>"
        f"<li>{_TICON['clock']}<span><b>Closes after 30 min</b>"
        "<small>Then confirm again.</small></span></li>"
        f"<li>{_TICON['lock']}<span><b>Nothing saved or logged</b>"
        "<small>Not what it shows, not what you type.</small></span></li></ul>"
        f"{form}<p class=tnote id=term-gate-note role=alert></p>"
        "<noscript><p class=tnote>The terminal needs the page script.</p></noscript>"
        "</div></section>"
        "<section class=tlive id=term-live hidden aria-label='Firstmate session'>"
        "<div class=twrap><div class=tframe id=term-frame><div id=term-screen></div></div>"
        "<p class=tframe__msg id=term-msg hidden></p>"
        "<button type=button class=tjump id=term-jump hidden>Jump to latest ↓</button></div>"
        f"<div class=tkeys id=term-keys role=toolbar aria-label='Terminal keys'>{keys}"
        "<span class=tkeys__gap></span>"
        "<button type=button id=term-smaller aria-label='Smaller text'>A−</button>"
        "<button type=button id=term-bigger aria-label='Bigger text'>A+</button>"
        "<button type=button id=term-fit aria-pressed=true>Fit</button></div>"
        "<div class=tpaste><textarea id=term-paste rows=1 maxlength=4096 disabled "
        "aria-label='Text to send' autocomplete=off autocapitalize=off spellcheck=false "
        "placeholder='Read-only'></textarea>"
        "<button type=button class='btn btn--s' id=term-send disabled>Send</button>"
        "<button type=button class='btn btn--s' id=term-send-enter disabled>Send + Enter</button>"
        "</div>"
        "<p class=tnote id=term-note role=status aria-live=polite></p></section></div>"
    )
    tail = (
        f"<script src='/vendor/{XTERM_JS}' integrity='{XTERM_SRI}'></script>"
        f"<script>{TERMINAL_SCRIPT}</script>"
    )
    return _page(
        "Terminal · Hive desk", body, "terminal", "", ctx, css=XTERM_CSS + TERMINAL_CSS, tail=tail
    )
