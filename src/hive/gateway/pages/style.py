# ruff: noqa: E501  (inline CSS)
"""The desk's one stylesheet, inlined into every page (the CSP allows no style file)."""

from __future__ import annotations

CSS = """
@font-face{font-family:"IBM Plex Mono";font-weight:400;font-display:swap;src:url(/fonts/ibm-plex-mono-400.woff2) format("woff2")}
@font-face{font-family:"IBM Plex Mono";font-weight:500;font-display:swap;src:url(/fonts/ibm-plex-mono-500.woff2) format("woff2")}
@font-face{font-family:"IBM Plex Mono";font-weight:600;font-display:swap;src:url(/fonts/ibm-plex-mono-600.woff2) format("woff2")}
@font-face{font-family:"IBM Plex Mono";font-weight:700;font-display:swap;src:url(/fonts/ibm-plex-mono-700.woff2) format("woff2")}
@font-face{font-family:"Familjen Grotesk";font-weight:400 700;font-display:swap;src:url(/fonts/familjen-grotesk.woff2) format("woff2")}
/* Comb (docs/design/comb-redesign.html): cream ground, honey for identity, one action colour */
:root{
--bg:#f4efe5;--card:#fffcf6;--surface:#e7dfcf;--ink:#29251e;--muted:#675e51;
--line:rgba(41,37,30,.13);--line2:rgba(41,37,30,.28);--field:rgba(41,37,30,.54);
--action:#8c6418;--action-ink:#fff;--honey:#e1ac10;--honey-ink:#2a1d04;--you-dot:#b8860b;--mark:#af7f0a;
--ok:#2c7a3c;--warn:#b5530b;--down:#c0341d;--unk:#7d7262;--run:#3a6b8c;--armed-ink:#fff;
--ok-t:rgba(44,122,60,.12);--warn-t:rgba(214,110,20,.15);--down-t:rgba(192,52,29,.11);--unk-t:rgba(41,37,30,.07);
--run-t:rgba(58,107,140,.13);--you-t:rgba(225,172,16,.24);
--comb-o:.08;--shadow:rgba(40,25,0,.16);--scrim:rgba(20,16,12,.5);
--fd:"Familjen Grotesk",system-ui,sans-serif;--ft:"Familjen Grotesk",system-ui,sans-serif;
--fm:"IBM Plex Mono",ui-monospace,Menlo,monospace;
--r1:4px;--r2:8px;--r3:12px;--rb:8px;--e:cubic-bezier(.2,.8,.2,1);
--hex:polygon(50% 0,100% 25%,100% 75%,50% 100%,0 75%,0 25%);
--arrow:polygon(12.5% 43.75%,68.25% 43.75%,49.75% 25.25%,58.58% 16.42%,92.17% 50%,58.58% 83.58%,49.75% 74.75%,68.25% 56.25%,12.5% 56.25%);
--gut:80px}
@media (prefers-color-scheme:dark){:root{
--bg:#0c0a08;--card:#181512;--surface:#26211b;--ink:#f7f2e8;--muted:#a39b8f;
--line:rgba(247,242,232,.12);--line2:rgba(247,242,232,.27);--field:rgba(247,242,232,.38);
--action:#c9a45a;--action-ink:#0c0a08;--honey:#f0b429;--honey-ink:#1a1206;--you-dot:#f0b429;--mark:#f0b429;
--ok:#6cc784;--warn:#f0904a;--down:#ff7a66;--unk:#a39b8f;--run:#8db9d8;--armed-ink:#1a1206;
--ok-t:rgba(108,199,132,.15);--warn-t:rgba(240,144,74,.17);--down-t:rgba(255,122,102,.15);--unk-t:rgba(247,242,232,.09);
--run-t:rgba(141,185,216,.15);--you-t:rgba(240,180,41,.2);
--comb-o:.07;--shadow:rgba(0,0,0,.5);--scrim:rgba(0,0,0,.6)}}
@media (max-width:1279px){:root{--gut:32px}}
@media (max-width:1023px){:root{--gut:24px}}
@media (max-width:640px){:root{--gut:16px}}
*,*::before,*::after{box-sizing:border-box}
/* one screen: the viewport is the page. Nothing scrolls the page; a long list scrolls inside its panel */
html{height:100%;background:var(--bg);-webkit-text-size-adjust:100%}
body{margin:0;height:100vh;height:var(--app-h,100dvh);display:flex;flex-direction:column;overflow:hidden;position:relative;isolation:isolate;
background:var(--bg);color:var(--ink);font:16px/1.45 var(--ft);-webkit-font-smoothing:antialiased}
.comb{position:absolute;inset:0;z-index:-1;width:100%;height:100%;color:var(--ink);opacity:var(--comb-o);pointer-events:none}
button,input,label,summary,a{touch-action:manipulation;-webkit-tap-highlight-color:transparent}
button,input,textarea,select{font:inherit;color:inherit}
:focus-visible{outline:2px solid var(--action);outline-offset:2px}
main{flex:1 1 0;min-height:0;width:min(1280px,100% - 2 * var(--gut));margin-inline:auto;display:flex;flex-direction:column;
overflow-x:hidden;overflow-y:auto;padding-bottom:calc(16px + env(safe-area-inset-bottom))}
h1{font:600 28px/1.1 var(--fd);letter-spacing:-.03em;margin:0}
h2{font:600 18px/1.2 var(--fd);letter-spacing:-.015em;margin:0}
p{margin:0}
a{color:inherit;text-decoration:underline;text-decoration-color:var(--line2);text-underline-offset:4px}
a:hover{text-decoration-color:currentColor}
.mute{color:var(--muted);font-size:15px}
code{font:13px var(--fm)}
/* the top bar: one width, one height and the same links on every page, so it never moves when the
   page changes. The links sit right after the brand, so the chips' changing labels cannot push them */
.chrome{flex:none;position:relative;z-index:20;display:grid;grid-template-columns:auto auto minmax(0,1fr);
grid-template-areas:"brand nav st";align-items:center;column-gap:28px;height:calc(57px + env(safe-area-inset-top));
padding:env(safe-area-inset-top) max(var(--gut),50% - 640px) 0;background:var(--card);border-bottom:1px solid var(--line)}
.chrome__brand{grid-area:brand;display:inline-flex;align-items:center;gap:9px;height:44px;font:600 21px/1 var(--fd);
letter-spacing:-.025em;color:var(--ink);text-decoration:none}
.chrome__brand svg{flex:none;width:24px;height:24px;fill:var(--honey)}
.chrome nav{grid-area:nav;display:flex;align-items:stretch;align-self:stretch;min-width:0}
.chrome nav a{position:relative;flex:none;display:inline-flex;align-items:center;justify-content:center;padding:0 12px;
font:500 15px var(--ft);color:var(--muted);text-decoration:none;white-space:nowrap;transition:color .15s var(--e)}
.chrome nav a:hover,.chrome nav a[aria-current]{color:var(--ink)}
.chrome nav a[aria-current]::after{content:"";position:absolute;left:12px;right:12px;bottom:-1px;height:3px;
border-radius:2px 2px 0 0;background:var(--mark)}
.chrome__st{grid-area:st;justify-self:end;display:flex;align-items:center;gap:8px;min-width:0}
/* below a laptop the chips share the brand's row and the links get a full-width row of their own */
@media (max-width:1023px){
.chrome{grid-template-columns:auto minmax(0,1fr);grid-template-areas:"brand st" "nav nav";height:calc(101px + env(safe-area-inset-top))}
.chrome__brand{height:52px}
.chrome nav{height:48px;margin-inline:calc(-1 * var(--gut));padding-inline:calc(var(--gut) - 12px);border-top:1px solid var(--line);
justify-content:space-between;overflow-x:auto;scrollbar-width:none}
.chrome nav::-webkit-scrollbar{display:none}}
@media (max-width:640px){
.chrome nav{padding-inline:4px}
.chrome nav a{flex:1 0 auto;padding:0 6px;min-width:44px;font-size:14px}
.chrome nav a[aria-current]::after{left:6px;right:6px}
.chrome__st .qchip{padding:0 10px;gap:6px}
.tchip__l{display:none}}
@media (max-width:400px){.chrome nav a{padding:0 2px}.chrome nav a[aria-current]::after{left:2px;right:2px}}
/* a 320 px slide-over still takes all seven in one row: smaller type, no padding to spare */
@media (max-width:374px){.chrome nav{padding-inline:0}.chrome nav a{min-width:0;font-size:13px}
.chrome{column-gap:10px}.chrome__brand{min-width:44px}.chrome__brand span{display:none}
.chrome__st{gap:6px}.chrome__st .qchip{padding:0 8px}}
/* the chips: firstmate's liveness and the busier plan window; a tap opens a panel under the bar */
.qwrap{margin:0}
.qchip{display:inline-flex;align-items:center;gap:8px;min-height:44px;padding:0 12px;border:1px solid var(--line);
border-radius:var(--rb);background:var(--card);font:500 14px var(--ft);color:var(--ink);cursor:pointer;list-style:none;white-space:nowrap}
.qchip::-webkit-details-marker{display:none}
.qwrap[open]>.qchip{border-color:var(--ink)}
#alerts{color:var(--muted)}#alerts[hidden]{display:none}
.dot{flex:none;width:9px;height:10px;clip-path:var(--hex);background:var(--unk)}
.qchip__bar{display:inline-block;width:28px;height:6px;border-radius:3px;background:var(--line);overflow:hidden}
.qchip__bar i{display:block;height:100%;background:var(--unk)}
.qchip--ok .dot,.qchip--ok .qchip__bar i,.dot--ok{background:var(--ok)}
.qchip--warn .dot,.qchip--warn .qchip__bar i,.dot--warn{background:var(--warn)}
.qchip--hot .dot,.qchip--hot .qchip__bar i,.dot--hot{background:var(--down)}
.qchip--hot{border-color:var(--down)}
.qchip--unknown{color:var(--muted)}
.qpop{position:absolute;right:max(var(--gut),50% - 640px);top:calc(100% + 8px);z-index:30;width:360px;
max-width:calc(100% - 2 * var(--gut));max-height:calc(var(--app-h,100dvh) - 140px);overflow-y:auto;padding:18px;
border:1px solid var(--line2);border-radius:var(--r3);background:var(--card);color:var(--ink);
box-shadow:0 16px 40px var(--shadow);font:15px/1.45 var(--ft);white-space:normal}
.qpop h3{display:flex;align-items:center;gap:8px;margin:0 0 4px;font:600 16px var(--fd)}
.qpop>div{display:flex;justify-content:space-between;align-items:center;gap:12px;min-height:44px;border-bottom:1px solid var(--line)}
.qpop b{font-weight:600;text-align:right}.qpop span,.qpop b{min-width:0}
.qpop .qpop__note{min-height:0;padding-top:8px;border-bottom:0;color:var(--muted);font-size:14px}
.qpop__alerts{margin-top:12px;padding-top:12px;border-top:1px solid var(--line)}
.qpop__alerts h3{font-size:15px}
.qpop__alerts p{margin:0 0 4px;color:var(--muted)}.qpop__alerts ol{margin:0 0 4px;padding-left:20px}.qpop__alerts li{padding:2px 0}
.qpop__alerts #alerts-note:empty{display:none}
.fmpop p{margin:0 0 8px;color:var(--muted)}.fmpop form{margin:0}
.fmpop>form{margin-top:12px}
.fmpop__term{margin-top:12px!important}
.wdpanel{margin-top:12px;padding-top:12px;border-top:1px solid var(--line);display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px}
.wdpanel>p{flex:1 1 160px;margin:0;color:var(--ink)}
.wdlog{flex-basis:100%;list-style:none;margin:0;padding:0;font-size:14px;color:var(--muted)}.wdlog li{padding:2px 0}
/* shared parts */
.ph{flex:none;display:flex;align-items:center;flex-wrap:wrap;gap:6px 12px;padding:20px 0 14px}
.ph .sub,.ph>.mute{font-size:15px;color:var(--muted)}
.ph__back{display:inline-flex;align-items:center;min-height:44px;font-size:15px;font-weight:600}
@media (max-width:640px){.ph{padding:14px 0 10px}h1{font-size:23px}.ph .sub,.ph>.mute{flex-basis:100%;font-size:14px}}
.meta,.stamp{font-size:13px;color:var(--muted)}
.hexn{display:inline-grid;place-items:center;flex:none;width:28px;height:32px;clip-path:var(--hex);background:var(--honey);
color:var(--honey-ink);font:600 14px/1 var(--ft)}
.hexn--q{background:var(--surface);color:var(--ink)}
.sl{display:flex;align-items:center;gap:8px;margin:18px 0 8px;font-size:13px;font-weight:600;color:var(--muted)}
.sl:first-child{margin-top:0}
.sc{display:inline-flex;align-items:center;gap:6px;height:26px;padding:0 10px;border-radius:999px;background:var(--unk-t);
font:500 13px var(--ft);color:var(--ink);white-space:nowrap;flex:none}
.sc i{flex:none;width:8px;height:9px;clip-path:var(--hex);background:var(--unk)}
.sc--ok{background:var(--ok-t)}.sc--ok i{background:var(--ok)}
.sc--warn{background:var(--warn-t)}.sc--warn i{background:var(--warn)}
.sc--down{background:var(--down-t)}.sc--down i{background:var(--down)}
.sc--run{background:var(--run-t)}.sc--run i{background:var(--run)}
.sc--you{background:var(--you-t)}.sc--you i{background:var(--you-dot)}
.sc--q{background:none;box-shadow:inset 0 0 0 1px var(--line2)}.sc--q i{background:var(--line2)}
.tag{display:inline-block;padding:3px 8px;border-radius:var(--r1);background:var(--surface);font:13px var(--fm);color:var(--ink);white-space:nowrap}
.bar{flex:1 1 auto;display:inline-block;height:4px;border-radius:2px;background:var(--line);overflow:hidden}
.bar i{display:block;height:100%;background:var(--ink)}
/* buttons: one filled primary (with its arrow), outlined secondaries */
.btn{min-height:44px}
.btn,button:not([class]):not([type=button]),button.quiet,button.danger{display:inline-flex;align-items:center;justify-content:center;gap:8px;
min-height:44px;padding:0 20px;border:1px solid transparent;border-radius:var(--rb);font:600 16px/1 var(--ft);cursor:pointer;
white-space:nowrap;text-decoration:none;transition:transform .15s var(--e),filter .15s var(--e),border-color .15s var(--e)}
.btn--p,button:not([class]):not([type=button]){background:var(--action);color:var(--action-ink)}
.btn--p::after,button:not([class]):not([type=button])::after{content:"";flex:none;width:12px;height:12px;margin-right:-2px;background:currentColor;
clip-path:var(--arrow);transition:transform .15s var(--e)}
.btn--s,button.quiet,button.danger{background:var(--card);border-color:var(--line2);color:var(--ink)}
.btn--danger,button.danger{color:var(--down)}
.btn--sm{padding:0 14px;font-size:14px}
.btn:hover,button:not([class]):not([type=button]):hover,button.quiet:hover,button.danger:hover{transform:translateY(-1px)}
.btn--p:hover,button:not([class]):not([type=button]):hover{filter:brightness(1.08)}
.btn--p:hover::after,button:not([class]):not([type=button]):hover::after{transform:translateX(3px)}
.btn:active,button:not([class]):not([type=button]):active,button.quiet:active,button.danger:active{transform:translateY(1px) scale(.985)}
.is-armed,button.is-armed{background:var(--warn)!important;border-color:var(--warn)!important;color:var(--armed-ink)!important}
.is-armed::after{display:none}
button:disabled,.btn[disabled]{opacity:.42;cursor:not-allowed;transform:none!important;filter:none!important}
.lnk{display:inline-flex;align-items:center;gap:4px;min-height:44px;padding:0;border:0;background:none;font-size:15px;font-weight:600;
color:var(--ink);cursor:pointer;text-decoration:underline;text-decoration-color:var(--line2);text-underline-offset:4px}
.lnk:hover{text-decoration-color:currentColor}
form{margin:0}
/* a field's edge is how you find it: --field keeps it at 3:1 or more on every ground (WCAG 1.4.11) */
textarea,input[type=text],select,.inp{width:100%;min-height:44px;padding:9px 14px;border:1px solid var(--field);border-radius:var(--rb);
background:var(--card);color:var(--ink);font:16px/1.4 var(--ft)}
textarea{min-height:5.5rem;resize:vertical}
::placeholder{color:var(--muted);opacity:1}
label{display:block;margin:8px 0 4px;font-size:14px;font-weight:600;color:var(--muted)}
label:has(input[type=checkbox]){display:flex;align-items:center;gap:8px;min-height:44px;color:var(--ink);font-weight:500}
input[type=checkbox]{width:20px;height:20px;accent-color:var(--action)}
details>summary{cursor:pointer}
.banner{padding:12px 14px;border-radius:var(--r2);background:var(--warn-t);color:var(--ink);font-size:15px;overflow-wrap:anywhere}
.banner.ok{background:var(--ok-t)}
.banner form{display:flex;flex-wrap:wrap;align-items:center;gap:10px 16px}.banner form p{flex-basis:100%}
.act-err,.act-warn,.act-ok{margin:8px 0 0;font:600 14px/1.4 var(--ft);color:var(--down);white-space:normal}
.act-ok{color:var(--ok)}
.empty{display:grid;place-content:center;justify-items:center;gap:12px;height:100%;min-height:150px;color:var(--muted);text-align:center}
.empty::before{content:"";width:44px;height:50px;clip-path:var(--hex);background:var(--surface)}
/* panels: anything long scrolls here, never the page */
.pgb{flex:1 1 0;min-height:0;display:grid;grid-template-rows:minmax(0,1fr);gap:16px}
.pn{position:relative;display:flex;flex-direction:column;min-height:0;min-width:0;background:var(--card);border:1px solid var(--line);
border-radius:var(--r3);container-type:inline-size;container-name:pn}
.pn__h{flex:none;display:flex;align-items:center;gap:10px;min-height:54px;padding:8px 16px;border-bottom:1px solid var(--line)}
.pn__h h2{white-space:nowrap}
.pn__h .meta{margin-left:auto;white-space:nowrap}
/* a panel's scrollbar is the only sign it scrolls, so its thumb takes --field's 3:1 too */
.pn__b{flex:1 1 auto;min-height:0;overflow-y:auto;overflow-x:hidden;padding:14px 16px 16px;overscroll-behavior:contain;
-webkit-overflow-scrolling:touch;scrollbar-width:thin;scrollbar-color:var(--field) transparent}
.pn__b--flush{padding:0 16px 8px}
/* phone tabs: the panels of a page become tabs under its title */
.tabs{display:none;flex:none;gap:4px;padding:4px;border-radius:var(--r2);background:var(--surface)}
.tabs button{flex:1 1 0;display:inline-flex;align-items:center;justify-content:center;gap:6px;min-width:0;height:44px;padding:0 10px;
border:0;border-radius:var(--r1);background:none;font:600 15px var(--ft);color:var(--muted);cursor:pointer;white-space:nowrap}
.tabs button[aria-selected=true]{background:var(--card);color:var(--ink);box-shadow:0 1px 2px var(--shadow)}
.tabs button small{font:400 13px var(--fm);color:var(--muted)}
@media (max-width:640px){
.pgb[data-tabs]{grid-template-columns:minmax(0,1fr)!important;grid-template-rows:auto minmax(0,1fr)!important;gap:8px}
.pgb[data-tabs]>.tabs--p{display:flex}
.pgb[data-tabs] [data-tp]:not(.is-on){display:none!important}
.pgb[data-tabs] .pn[data-tp]>.pn__h{display:none}}
/* a modal sheet over the page */
.sheet{width:min(560px,calc(100vw - 24px));max-height:min(80vh,calc(100dvh - 24px));margin:auto;padding:0;
background:var(--card);color:var(--ink);border:1px solid var(--line2);border-radius:var(--r3);
box-shadow:0 16px 48px var(--shadow);overflow:hidden}
.sheet[open]{display:flex;flex-direction:column;animation:sheet-in .18s var(--e)}
.sheet.is-restored[open]{animation:none}
.sheet{cursor:pointer}.sheet>*{cursor:auto}
.sheet::backdrop{background:var(--scrim)}
.sheet__head{flex:none;display:flex;align-items:center;gap:8px;min-height:54px;padding:4px 6px 4px 16px;border-bottom:1px solid var(--line)}
.sheet__head>*{min-width:0}
.sheet__head h2{flex:1;overflow-wrap:anywhere}
.sheet__x{flex:none;min-width:44px;min-height:44px;padding:0;border:0;border-radius:var(--r2);background:transparent;color:var(--ink);font-size:26px;line-height:1;cursor:pointer}
.sheet__x:hover{background:var(--surface)}
.sheet__list{display:flex;flex-direction:column;overflow-y:auto;overflow-x:hidden;min-height:0;padding:0 16px env(safe-area-inset-bottom);
-webkit-overflow-scrolling:touch;overscroll-behavior:contain}
.sheet__meta{flex:none;margin:0;padding:12px 16px;font-size:15px;color:var(--muted);border-bottom:1px solid var(--line)}
.sheet__meta b{color:var(--ink);font-weight:600}
.sheet__text{margin:0;padding:14px 0;font-size:16px;line-height:1.55;white-space:pre-wrap;overflow-wrap:anywhere}
.sheet__about{flex:none;margin:0;padding:12px 16px 4px;font-size:16px;line-height:1.5;overflow-wrap:anywhere}
.sheet__empty{margin:0;padding:14px 0;font-size:15px;color:var(--muted)}
@keyframes sheet-in{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{transition:none!important;animation:none!important}}
/* chat: one screen; the thread scrolls inside its panel and the box stays at the panel's foot */
.chatpage main{width:min(880px,100% - 2 * var(--gut))}
.chatpage .banner{flex:none;margin-bottom:12px}
.chatpn{flex:1 1 0;min-height:0}
.thread{display:flex;flex-direction:column;gap:20px}
.msg{display:flex;flex-direction:column;align-items:flex-start;gap:5px;max-width:86%;min-width:0}
.msg.me{align-self:flex-end;align-items:flex-end}
.msg.fm{align-self:flex-start}
.bubble{max-width:100%;white-space:pre-wrap;overflow-wrap:anywhere}
.msg.me .bubble{padding:11px 15px;border-radius:var(--r3);background:var(--surface)}
.msg.me .rc{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:9px 14px;border:1px solid var(--line2);background:var(--card)}
.rc>span:last-child{flex-basis:100%}
.msg.fm .bubble{padding-left:14px;border-left:3px solid var(--honey)}
.msg__who{display:flex;align-items:center;gap:6px;font-size:13px;font-weight:600;color:var(--muted)}
.msg__who i{width:10px;height:11px;clip-path:var(--hex);background:var(--honey)}
.msg .meta{display:block}
.msg.is-err .meta{display:flex;flex-wrap:wrap;align-items:center;gap:4px 10px;color:var(--down)}
.msg .meta button{min-height:44px;padding:0 16px}
.dock{flex:none;padding:12px 16px;border-top:1px solid var(--line)}
.dock form{display:flex;gap:8px;align-items:flex-end}
.dock label{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
.dock textarea{flex:1;min-width:0;height:44px;min-height:44px;padding:10px 14px;resize:none;margin:0}
.dock .btn{flex:none}
#jump{position:absolute;left:50%;bottom:calc(var(--dock-h,69px) + 12px);transform:translateX(-50%);z-index:2;border-radius:999px;
box-shadow:0 4px 14px var(--shadow)}
#jump[hidden]{display:none}
@media (max-width:640px){.msg{max-width:94%}.chatpage .dock{padding:10px 12px}}
/* the desk: three panels side by side over the delegate bar. Below a laptop the two right-hand
   panels share one column behind tabs; a phone tabs all three */
.sr{position:absolute!important;width:1px;height:1px;margin:-1px;padding:0;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;border:0}
body.wide main:has(>.screen){width:100%;padding-bottom:0;overflow:hidden}
.screen{flex:1 1 0;min-height:0;display:flex;flex-direction:column}
.desk{width:min(1280px,100% - 2 * var(--gut));margin-inline:auto;padding:16px 0;
grid-template-columns:minmax(0,5fr) minmax(0,4fr) minmax(0,3fr)}
.desk--two{grid-template-columns:minmax(0,7fr) minmax(0,5fr)}
.dcol{display:contents}
@media (min-width:641px) and (max-width:1279px){
.desk{grid-template-columns:minmax(0,7fr) minmax(0,5fr)}
.desk .dcol{display:flex;flex-direction:column;gap:8px;min-height:0;min-width:0}
.desk .dcol>.tabs--r{display:flex}
.desk .dcol>.pn{flex:1 1 0}
.desk:not(.desk--two) .dcol>.pn:not(.is-ron){display:none}
.desk:not(.desk--two) .dcol>.pn>.pn__h{display:none}}
@media (min-width:641px) and (max-width:1023px){
.desk{grid-template-columns:minmax(0,1fr);grid-template-rows:minmax(0,3fr) minmax(0,2fr)}}
@media (max-width:640px){.desk{padding:10px 0}}
/* Needs you: decision cards (one open at a time), then held, blocked and queued tickets as rows */
.nyl__body{display:flex;flex-direction:column}
.nyg{display:flex;flex-direction:column}
.nyg--cards{gap:8px}
.nyx__head{color:inherit;text-decoration:none;cursor:pointer}
.nyx__head:focus-visible{outline-offset:-2px}
.nyx__more{display:none}
.nyx.is-primary .nyx__more{display:block}
.nyc{border:1px solid var(--line);border-radius:var(--r3);background:var(--card)}
.nyc__head{display:flex;flex-wrap:wrap;align-items:center;gap:0 10px;width:100%;min-height:50px;padding:10px 16px;border-radius:var(--r3)}
.nyc__head:hover{background:var(--surface)}
.nyx.is-primary .nyc__head{padding-bottom:0;cursor:default}
.nyx.is-primary .nyc__head:hover,.nyc--calm .nyc__head:hover{background:none}
.nyc__where{flex:0 1 auto;display:flex;align-items:baseline;gap:8px;min-width:0;font-size:15px;font-weight:600;overflow:hidden;white-space:nowrap}
.nyc__proj{min-width:0;overflow:hidden;text-overflow:ellipsis}
.nyc__where code{font-weight:400;color:var(--muted);overflow:hidden;text-overflow:ellipsis}
.nyc__chev{margin-left:auto;font-size:13px;font-weight:600;color:var(--muted)}
.nyx.is-primary .nyc__chev{display:none}
.nyc__head .nyc__what{flex-basis:100%;min-width:0;padding-top:4px;font-size:16px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.nyx.is-primary .nyc__head .nyc__what,.nyc__body .nyc__what{padding-top:10px;font:600 19px/1.3 var(--fd);letter-spacing:-.012em;
white-space:normal;overflow-wrap:anywhere}
.nyc__body .nyc__what{padding-top:0}
.nyc__body{padding:0 16px 16px}
.nyc__mean{margin-top:4px;font-size:16px;color:var(--muted);overflow-wrap:anywhere}
.nyc__mean a{color:inherit}
.opts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:14px}
.opt{display:flex;align-items:center;gap:12px;min-height:58px;padding:8px 16px;border:1px solid var(--line2);border-radius:var(--r2);
background:var(--card);color:var(--ink);font:16px/1.3 var(--ft);text-align:left;cursor:pointer;
transition:transform .15s var(--e),border-color .15s var(--e)}
.opt b{flex:none;font:500 13px var(--fm);color:var(--muted)}
.opt span{min-width:0;display:flex;flex-direction:column;overflow-wrap:anywhere}
.opt em{font-style:normal;font-size:13px;font-weight:600;color:var(--action)}
.opt.is-rec{border:2px solid var(--action);padding:7px 15px}
.opt:hover{transform:translateY(-1px)}
.opt[aria-pressed=true]{background:var(--ink);border-color:var(--ink);color:var(--bg)}
.opt[aria-pressed=true] b,.opt[aria-pressed=true] em{color:var(--bg)}
.nyc__act{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}
.nyc__act>.btn{flex:none}
input.nyi__reply{flex:1 1 100%;min-width:0;
min-height:44px}
.nyc__links{display:flex;flex-wrap:wrap;align-items:flex-start;gap:0 20px;margin-top:2px}
.nyc__full>summary{list-style:none}
.nyc__full>summary::-webkit-details-marker{display:none}
.nyc__full[open]{flex-basis:100%}
.nyc__full p{padding-bottom:8px;font-size:15px;color:var(--muted);white-space:pre-wrap;overflow-wrap:anywhere}
.nyc__form label:first-child{margin-top:14px}
.nyc__form>.btn{margin-top:10px}
.nyx__desc{margin:10px 0 0;padding-left:10px;border-left:2px solid var(--line2);font-size:15px;line-height:1.45;color:var(--muted);
overflow-wrap:anywhere;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:4;line-clamp:4;overflow:hidden}
.nyx__desc[data-ready]{cursor:pointer}
.nyx__desc[data-ready]:hover{color:var(--ink)}
.nyx__desc[hidden]{display:none}
.nyx__desc.is-pending{animation:nyx-pulse 1.4s ease-in-out infinite}
.nyr{border-bottom:1px solid var(--line)}
.nyr:last-child{border-bottom:0}
.nyr .nyx__head{display:flex;align-items:center;gap:10px;min-height:48px;padding:6px 0}
.nyr .nyx__head:hover .r__t{text-decoration:underline;text-decoration-color:var(--line2);text-underline-offset:4px}
.r__t{flex:1 1 auto;min-width:0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;font-size:15px}
.r__p{flex:0 1 auto;min-width:0;max-width:40%;font:13px var(--fm);color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.nyr.is-primary .r__t{white-space:normal;overflow-wrap:anywhere;font-weight:600}
.nyr .nyx__more{padding:0 0 12px}
.nyr__why{font-size:15px;color:var(--muted);overflow-wrap:anywhere}
.nyx.is-flip{transition:transform .3s var(--e);will-change:transform}
.nyx.is-lifted{z-index:2}
.nyx.is-opening .nyx__more{animation:nyx-in .28s ease-out both}
@keyframes nyx-in{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:none}}
@keyframes nyx-pulse{50%{opacity:.45}}
.nyq{display:flex;flex-wrap:wrap;gap:0 20px;margin-top:4px}
@container pn (max-width:440px){
.opts{grid-template-columns:minmax(0,1fr)}
.nyc__act>.btn{flex:1 1 auto}
.nyr .nyx__head{flex-wrap:wrap;gap:2px 10px;padding:8px 0}
.r__t{order:3;flex-basis:100%;white-space:normal}}
/* Projects: one row each; a tap focuses it (the delegate bar retargets), a second tap opens its detail */
.pcs{display:flex;flex-direction:column}
.pcs>.stamp{padding-top:8px}
.pc{flex:none;position:relative;display:grid;grid-template-columns:minmax(0,1fr) auto 76px;align-items:center;gap:10px;
min-height:44px;padding:8px;margin:0 -8px;border-radius:var(--r2);color:inherit;text-decoration:none;cursor:pointer}
.pc::after{content:"";position:absolute;left:8px;right:8px;bottom:0;height:1px;background:var(--line)}
.pc:last-of-type::after,.pc.is-selected::after,.pc:has(+.pc.is-selected)::after{display:none}
.pc:hover{background:var(--surface)}
.pc.is-selected{background:var(--you-t)}
.pc__n{display:flex;flex-direction:column;min-width:0}
.pc__name{font-size:15px;font-weight:600;line-height:1.35;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.pc__n small{font-size:13px;color:var(--muted);overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.pc__mae{white-space:nowrap}
.aka{margin-left:6px;font:400 13px var(--fm);color:var(--muted);white-space:nowrap}
.pc__prog{display:flex;align-items:center;gap:6px;font:13px var(--fm);color:var(--muted)}
.pc--blocked .bar i{background:var(--down)}
@container pn (max-width:330px){.pc{grid-template-columns:minmax(0,1fr) auto auto}.pc__prog .bar{display:none}}
/* the review list: open Lavish sessions; the list scrolls and "+N more" stays under it */
.rvs{flex:1 1 auto;min-height:0;display:flex;flex-direction:column}
.rv__box{flex:1 1 auto;min-height:0;display:flex;flex-direction:column}
.rv__list{flex:0 1 auto;min-height:0;overflow-y:auto;overflow-x:hidden;padding:6px 16px;overscroll-behavior:contain;
-webkit-overflow-scrolling:touch;scrollbar-width:thin;scrollbar-color:var(--field) transparent}
.rvrow{display:flex;align-items:center;gap:10px;min-height:62px;padding:6px 0;border-bottom:1px solid var(--line)}
.rvrow:last-child{border-bottom:0}
.rvrow>form{flex:none;display:flex}
.rv{flex:1 1 auto;min-width:0;min-height:44px;display:flex;flex-direction:column;justify-content:center;gap:3px;color:inherit;text-decoration:none}
.rv:hover .rv__title{text-decoration:underline;text-decoration-color:var(--line2);text-underline-offset:4px}
.rv__title{overflow:hidden;white-space:nowrap;text-overflow:ellipsis;font-size:15px;font-weight:500}
.rv__meta{display:flex;align-items:center;gap:8px;min-width:0;font:13px var(--fm);color:var(--muted)}
.rv__meta .sc{height:22px}
.rv__meta:empty{display:none}
.rv__proj{min-width:0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.rv__done{flex:none;padding:1px 7px;border-radius:var(--r1);background:var(--surface);color:var(--ink);font:13px var(--fm);white-space:nowrap}
form.is-sending .rv__x{opacity:.5}
.rv__morebtn{flex:none;align-self:flex-start;display:inline-flex;align-items:center;min-height:44px;margin:0 16px 4px;padding:0;border:0;
background:none;color:var(--ink);font:600 15px var(--ft);cursor:pointer;text-decoration:underline;text-decoration-color:var(--line2);text-underline-offset:4px}
.rv__morebtn:hover{text-decoration-color:currentColor}
.rvs__nudge{flex:none;display:flex;flex-wrap:wrap;align-items:center;gap:6px 8px;margin:10px 16px 0;padding:6px 6px 6px 12px;
border-radius:var(--r2);background:var(--you-t);font-size:14px}
.rvs__nudge>span{flex:1 1 100%;min-width:0;padding:4px 0}
.rvs__nudge form{display:flex}
.sheet__list>*{flex:none}
.sheet__list .rvrow,.sheet__list>a.rv{min-height:56px;border-bottom:1px solid var(--line)}
.sheet__list>a.rv:last-child{border-bottom:0}
/* the delegate bar: who it sends to, the goal, Delegate and when the desk last read firstmate */
.dbar-wrap{position:relative;flex:none;padding:10px 0 calc(10px + env(safe-area-inset-bottom));background:var(--card);border-top:1px solid var(--line)}
.dbar__row{width:min(1280px,100% - 2 * var(--gut));margin-inline:auto;display:flex;align-items:center;gap:8px}
form.dbar{display:contents}
.dbar__target{flex:none;max-width:40%;height:44px;padding:0 14px;border-radius:var(--rb);background:var(--you-t);
font-size:15px;font-weight:600;line-height:44px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
input.dbar__in{flex:1 1 200px;width:auto;min-width:0;
min-height:44px}
.dbar__go{flex:none;
min-height:44px}
.dbar__row>.stamp{flex:none;margin-left:8px;white-space:nowrap}
.dbar.is-sending>*{opacity:.7}
.dbar-flash{position:absolute;left:50%;bottom:calc(100% + 10px);z-index:5;transform:translateX(-50%);width:max-content;
max-width:calc(100% - 2 * var(--gut));padding:10px 16px;border-radius:var(--r2);background:var(--ink);color:var(--card);
font:600 15px/1.35 var(--ft);box-shadow:0 8px 24px var(--shadow)}
.dbar-flash--err{background:var(--down)}
.dbar-flash[hidden]{display:none}
@media (max-width:640px){
.dbar__row{flex-wrap:wrap}
.dbar__target{order:1;height:32px;padding:0 10px;font-size:14px;line-height:32px;max-width:calc(100% - 140px)}
.dbar__row>.stamp{order:2;margin-left:auto}
input.dbar__in{order:3;flex:1 1 calc(100% - 150px)}
.dbar__go{order:4}}
/* config: merge posture beside the worker panels; phone tabs between the two */
.cfg{grid-template-columns:minmax(0,7fr) minmax(0,5fr)}
.cfg__col{display:flex;flex-direction:column;gap:16px;min-height:0;min-width:0}
.cfg__col>.cfg__prof{flex:1 1 0}
.cfg__col>.pn:last-child{flex:none}
.cfg .unk{color:var(--down);font-weight:600;overflow-wrap:anywhere}
.tbl{width:100%;border-collapse:collapse;font-size:15px}
.tbl th{position:sticky;top:0;z-index:1;height:40px;padding:0 12px 0 0;background:var(--card);border-bottom:1px solid var(--line2);
font-size:13px;font-weight:600;color:var(--muted);text-align:left;white-space:nowrap}
.tbl td{height:40px;padding:0 12px 0 0;border-bottom:1px solid var(--line)}
.tbl th:last-child,.tbl td:last-child{padding-right:0}
.tbl tr:last-child td{border-bottom:0}
.tbl td:first-child{font-weight:600;overflow-wrap:anywhere}
.prof{list-style:none;margin:0;padding:0}
.prof li{display:flex;flex-direction:column;gap:6px;padding:10px 0;border-bottom:1px solid var(--line)}
.prof li:first-child{padding-top:0}
.prof li:last-child{border-bottom:0}
.prof__w{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;font-size:15px}
.ms{display:flex;flex-wrap:wrap;align-items:center;gap:6px}
.kv{display:grid;grid-template-columns:auto 1fr;gap:10px 18px;align-items:center;margin:0;font-size:16px}
.kv dt{color:var(--muted);font-size:15px}
.kv dd{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin:0;min-width:0}
@container pn (max-width:400px){
.tbl thead{display:none}
.tbl tr{display:flex;flex-wrap:wrap;align-items:center;gap:6px 10px;padding:8px 0;border-bottom:1px solid var(--line)}
.tbl tr:last-child{border-bottom:0}
.tbl td{height:auto;padding:0;border:0}
.tbl td:first-child{flex-basis:100%}}
form.is-sent button{opacity:.75}
/* the Machine page: the chain that has to hold and the upcoming risks; read-only */
/* the Machine page: the chain that has to hold beside the upcoming risks; read-only */
.mach{grid-template-columns:minmax(0,7fr) minmax(0,5fr)}
@media (min-width:641px) and (max-width:1023px){.mach{grid-template-columns:minmax(0,1fr);grid-template-rows:fit-content(60%) minmax(0,1fr)}}
.mach__sum{display:flex;flex-wrap:wrap;gap:6px;list-style:none;margin:0;padding:0}
.mach__term{font-size:15px}
@media (max-width:640px){.mach__term{display:none}}
.chain{position:relative;list-style:none;margin:0;padding:0}
.chain::before{content:"";position:absolute;left:13px;top:26px;bottom:26px;width:2px;background:var(--line2)}
.chain--one::before{display:none}
.ch{position:relative;display:grid;grid-template-columns:28px minmax(0,1fr) auto;column-gap:14px;align-items:center;min-height:48px;padding:3px 0}
.ch__n{display:grid;place-items:center;width:28px;height:32px;clip-path:var(--hex);background:var(--unk)}
.ch--ok .ch__n{background:var(--ok)}.ch--warn .ch__n{background:var(--warn)}.ch--down .ch__n,.ch--urgent .ch__n{background:var(--down)}
.ch__n svg{width:14px;height:14px;fill:none;stroke:var(--card);stroke-width:2.6;stroke-linecap:round;stroke-linejoin:round}
.ch__b{min-width:0}
.ch__l{font-size:15px;line-height:1.3;overflow-wrap:anywhere}
.ch__l b{font-weight:600}
.ch__d{font-size:13px;color:var(--muted);overflow-wrap:anywhere}
.ch__d time,.risk .d time{white-space:nowrap}
.ch--urgent .ch__l b{color:var(--down)}
.mach .sl{margin:14px 0 4px}
.tl{position:relative;height:96px;margin:2px 14px 8px}
.tl__ax{position:absolute;left:0;right:0;top:42px;height:10px;border-radius:5px;background:var(--surface);overflow:hidden}
.tl__w{position:absolute;top:0;bottom:0;left:0;background:var(--down-t)}
.tl__m{position:absolute;top:36px;width:20px;height:22px;margin-left:-10px;clip-path:var(--hex);background:var(--unk)}
.tl__m--ok{background:var(--ok)}.tl__m--warn{background:var(--warn)}.tl__m--down{background:var(--down)}
.tl__lab{position:absolute;transform:translateX(-50%);font-size:13px;font-weight:600;white-space:nowrap}
.tl__lab--up{top:10px}.tl__lab--dn{top:64px}
.tl__lab--first{transform:translateX(-12px)}
.risks{list-style:none;margin:0;padding:0}
.risk{display:grid;grid-template-columns:92px minmax(0,1fr) auto;gap:2px 12px;align-items:center;padding:10px 0;border-top:1px solid var(--line)}
.risks .risk:first-child{border-top:0}
.tl+.risks .risk:first-child{border-top:1px solid var(--line)}
.risk b{font-weight:600;overflow-wrap:anywhere}
.risk .sc{justify-self:start}
.risk .d{grid-column:2/4;font-size:13px;color:var(--muted);overflow-wrap:anywhere}
.risk .when{font-size:15px;font-weight:600;text-align:right;white-space:nowrap}
.risk--urgent b,.risk--urgent .when{color:var(--down)}
@container pn (max-width:300px){.risk{grid-template-columns:minmax(0,1fr) auto}.risk .sc{grid-column:1/3}.risk .d{grid-column:1/3}}
/* the Run page: the Linux fixes, the Windows fixes and the last runs, side by side */
.run{grid-template-columns:repeat(3,minmax(0,1fr))}
@media (min-width:641px) and (max-width:1179px){
.run{grid-template-columns:minmax(0,1.25fr) minmax(0,1fr);grid-template-rows:fit-content(55%) minmax(0,1fr)}
.run>[data-tp=runs]{grid-column:2;grid-row:1/3}.run>[data-tp=linux]{grid-column:1;grid-row:1}.run>[data-tp=win]{grid-column:1;grid-row:2}}
.ars,.runs{list-style:none;margin:0;padding:0}
.ar{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:2px 12px;align-items:center;padding:10px 0;border-bottom:1px solid var(--line)}
.ar:first-child{padding-top:0}
.ar:last-child{border-bottom:0}
.ar__t{display:flex;align-items:baseline;gap:8px;min-width:0;flex-wrap:wrap}
.ar__t b{font-weight:600;font-size:15px}
.ar__d{grid-column:1;min-width:0;font-size:13px;color:var(--muted);overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.ar__cmd{grid-column:1;min-width:0;font:13px/1.4 var(--fm);color:var(--muted);overflow-wrap:anywhere}
.ar__off{grid-column:1/-1;font-size:13px;font-weight:600;color:var(--muted)}
.ar>form,.ar>button{grid-row:1/4;grid-column:2;display:flex;flex-wrap:wrap;align-items:center;justify-content:flex-end;gap:6px;margin:0;max-width:260px}
.ar form label{font-size:13px;color:var(--muted)}
.ar select{width:auto;min-height:44px;padding:0 10px}
.ar button{white-space:nowrap}
.ar--off .ar__t,.ar--off .ar__d,.ar--off .ar__cmd{opacity:.72}
/* the first tap: the card turns amber, its words take the whole width, read first, then tap */
.ar:has(form[data-step]){margin:0 -10px;padding:10px;border-radius:var(--r2);background:var(--warn-t);border-bottom-color:transparent}
.ar>form[data-step],.ar>form.is-shown{grid-column:1/-1;grid-row:auto;max-width:none;justify-content:flex-start;margin-top:8px}
.ar:has(form[data-step]) .ar__d,.ar:has(form[data-step]) .ar__cmd{display:none}
.ar form .act-warn,.ar form .act-err,.ar form .act-ok{flex-basis:100%;margin:0;text-align:left}
.ar form .act-warn{order:-1;padding:8px 10px;border-radius:var(--r1);background:var(--card);font:600 14px/1.45 var(--ft);color:var(--ink);overflow-wrap:anywhere}
.ar--down form .act-warn{box-shadow:inset 3px 0 0 var(--down)}
.note{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:10px 12px;margin-bottom:6px;border-radius:var(--r2);background:var(--warn-t);font-size:14px}
.note>span:last-child{flex:1 1 220px}
.run__lead{margin-bottom:6px}
.fold{margin-top:6px;border-top:1px solid var(--line)}
.fold summary{display:flex;align-items:center;min-height:44px;font-size:14px;font-weight:600;color:var(--muted);cursor:pointer;list-style:none}
.fold summary::-webkit-details-marker{display:none}
.fold summary::after{content:"Show";margin-left:auto;color:var(--ink);text-decoration:underline;text-decoration-color:var(--line2);text-underline-offset:4px}
.fold[open] summary::after{content:"Hide"}
.fold ul{list-style:none;margin:0;padding:0}
.fold li{padding:6px 0;font-size:14px}
.fold li span{display:block;color:var(--muted);font-size:13px}
/* a project: what needs you beside its crews and backlog (the board's Needs page) */
.ph__back{margin-right:4px;font-size:15px}
.npg{grid-template-columns:minmax(0,7fr) minmax(0,5fr)}
@media (min-width:641px) and (max-width:1023px){.npg{grid-template-columns:minmax(0,1fr);grid-template-rows:minmax(0,3fr) minmax(0,2fr)}}
.npg .pn__b>.sl:first-child{margin-top:0}
.crews,.bls{list-style:none;margin:0;padding:0}
.crew,.bl{padding:10px 0;border-bottom:1px solid var(--line)}
.crew:last-child,.bl:last-child{border-bottom:0}
.crew__top{display:flex;align-items:center;flex-wrap:wrap;gap:4px 10px}
.crew__top b{flex:1 1 auto;min-width:0;font-weight:600;overflow-wrap:anywhere}
.crew__top .lnk{min-height:44px}
.crew__d{font-size:14px;color:var(--muted);overflow-wrap:anywhere}
.crew__d span{display:block}
.crew__ctl{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 0}
.crew__ctl .act-warn,.crew__ctl .act-err{flex-basis:100%;margin:0}
.bl{display:grid;grid-template-columns:auto minmax(0,1fr);gap:4px 10px;align-items:start}
.bl .sc{margin-top:1px}
.bl__t{display:block;font-weight:600;overflow-wrap:anywhere}
.bl__d{margin-top:2px;font-size:14px;color:var(--muted);overflow-wrap:anywhere}
.bl__d code,.crew__d code,.ph .sub code{font:13px var(--fm)}
.tk>summary{display:inline-flex;align-items:center;min-height:44px;font-size:14px;font-weight:600;color:var(--ink);
text-decoration:underline;text-decoration-color:var(--line2);text-underline-offset:4px}
.fold.tk{margin:0 0 6px}
.fold.tk>summary{display:flex;color:var(--ink);text-decoration:none}
.tk[open]{padding-bottom:6px}
.tk button{margin-top:10px}
/* a crew's live output, and the confirm and result steps */
.watch{flex:1 1 0;min-height:0}
.tail{margin:0;padding:14px 16px;background:#16140f;color:#e9dfc9;border-radius:0 0 calc(var(--r3) - 1px) calc(var(--r3) - 1px);
font:13px/1.5 var(--fm);white-space:pre-wrap;overflow-wrap:anywhere}
.step{width:min(640px,100%);display:flex;flex-direction:column;align-items:flex-start;gap:12px}
.step--ask{padding:22px;border:1px solid var(--line);border-radius:var(--r3);background:var(--card)}
.step--ask form p{font-size:16px;overflow-wrap:anywhere}
.step__act{display:flex;flex-wrap:wrap;align-items:center;gap:12px 20px;margin-top:16px}
.step .banner{align-self:stretch}
.runrow{display:grid;grid-template-columns:auto minmax(0,1fr);gap:2px 12px;align-items:center;padding:10px 0;border-bottom:1px solid var(--line)}
.runrow:first-child{padding-top:0}
.runrow:last-child{border-bottom:0}
.runrow b{font-weight:600;overflow-wrap:anywhere}
.runrow .d{grid-column:2;font-size:13px;color:var(--muted);overflow-wrap:anywhere}
.runrow .d time{white-space:nowrap}
@container pn (max-width:340px){.ar{grid-template-columns:minmax(0,1fr)}.ar>form{grid-row:auto;grid-column:1;justify-content:flex-start;max-width:none;margin-top:6px}
.ar>button{grid-row:auto;grid-column:1;justify-self:start;margin-top:6px}}
/* the Sign-ins page: a card per sign-in, under the one renewal that may be running */
.sgb{display:flex;flex-direction:column;gap:14px;overflow-y:auto;overscroll-behavior:contain;padding:2px}
.sg{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;list-style:none;margin:0;padding:0}
.sgrow{display:flex;flex-direction:column;gap:4px;min-width:0;padding:18px;border:1px solid var(--line);border-radius:var(--r3);background:var(--card)}
.sgrow--urgent{border-color:var(--down)}
.sgrow__top{display:flex;align-items:center;gap:8px;justify-content:space-between}
.sgrow__top b{font-size:16px;font-weight:600;overflow-wrap:anywhere}
.sgrow__big{margin-top:8px;font:600 28px/1.15 var(--fd);letter-spacing:-.02em;overflow-wrap:anywhere}
.sgrow__d{font-size:15px;color:var(--muted);overflow-wrap:anywhere}
.sgrow__d time{white-space:nowrap}
.sgrow__act{margin-top:auto;padding-top:12px;display:flex;flex-direction:column;align-items:flex-start;gap:6px;min-width:0}
.sgrow__act form{margin:0;display:flex;flex-direction:column;align-items:flex-start;gap:6px;width:100%}
.sgrow__act button{white-space:nowrap}
.sgrow__act .act-warn,.sgrow__act .act-err{margin:0;font-size:14px}
.sgrow__why{font-size:14px;color:var(--muted);overflow-wrap:anywhere}
.sgreach{margin-top:4px;min-width:0}
.sgreach>summary{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 8px;min-height:44px;padding-block:9px;list-style:none;font-size:14px;color:var(--ink)}
.sgreach>summary::-webkit-details-marker{display:none}
.sgreach__w{flex:1 1 13em;min-width:0;overflow-wrap:anywhere;text-decoration:underline dotted var(--line2);text-underline-offset:4px}
.sgreach__why{font-size:13px;color:var(--muted);overflow-wrap:anywhere}
.sgflow{flex:none}
.sgflow:empty{display:none}
.sgf{padding:18px 22px;border:2px solid var(--honey);border-radius:var(--r3);background:var(--card)}
.sgf--ok{border-color:var(--ok)}.sgf--failed{border-color:var(--down)}.sgf--quiet{border-color:var(--line2)}
.sgf>p+p,.sgf>*+.meta{margin-top:10px}
.sgf__h{display:flex;align-items:center;gap:6px 10px;flex-wrap:wrap}
.sgf__h form{margin:0 0 0 auto}
.sgf__title{font:600 18px var(--fd);letter-spacing:-.01em}
.sgf>p:not(.sgf__note){margin-top:8px}
.sgf__steps{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr));gap:12px;margin:14px 0 0;padding:0;
list-style:none;counter-reset:sgstep}
.sgf__steps>li{position:relative;display:flex;flex-direction:column;align-items:flex-start;gap:8px;min-width:0;padding:14px 14px 14px 58px;
border-radius:var(--r2);background:var(--surface);counter-increment:sgstep;font-size:15px;overflow-wrap:anywhere}
.sgf__steps>li::before{content:counter(sgstep);position:absolute;left:14px;top:12px;display:grid;place-items:center;width:30px;height:34px;
clip-path:var(--hex);background:var(--honey);color:var(--honey-ink);font-weight:700}
.sgf__steps b,.sgf__steps label{font-weight:600;color:var(--ink)}
.sgf__link{display:flex;align-items:center;justify-content:center;align-self:stretch;min-height:56px;padding:8px 18px;border-radius:var(--rb);
background:var(--action);color:var(--action-ink);font:600 16px var(--ft);text-decoration:none;text-align:center;
transition:transform .15s var(--e),filter .15s var(--e)}
.sgf__link:hover{filter:brightness(1.06)}
.sgf__link:active{transform:translateY(1px)}
.sgf__host{font:13px var(--fm);color:var(--muted)}
.sgf__code{display:inline-block;padding:8px 14px;border:1.5px dashed var(--line2);border-radius:var(--r1);background:var(--card);
font:600 22px var(--fm);letter-spacing:.12em}
.sgf__paste{display:flex;flex-wrap:wrap;gap:8px;margin:0;align-self:stretch}
.sgf__paste input{flex:1 1 160px;min-width:0;width:auto;font:15px var(--fm)}
.sgf__paste .act-err{flex-basis:100%;margin:0}
.sgf__note{margin-top:8px;font-size:14px;font-weight:600;color:var(--down)}
.sgf__steps .sgf__note{margin-top:0}
@media (max-width:1023px){.sg{grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr))}}
@media (max-width:640px){.sg{grid-template-columns:minmax(0,1fr)}.sgrow{padding:14px}.sgrow__big{font-size:22px;margin-top:4px}.sgf{padding:14px}
.sgf__steps>li{padding-left:54px}}
"""
