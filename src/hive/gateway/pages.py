# ruff: noqa: E501  (inline CSS)
"""HTML for the desk. Plain escaped strings plus one fixed inline script (``SCRIPT``,
pinned by hash in the CSP) for local times and self-refresh. Forms post to ``/act/*``."""

from __future__ import annotations

import base64
import hashlib
import re
from datetime import UTC, datetime, timedelta
from html import escape as esc
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from hive.gateway import fmconfig, machine, runqueue, signins
from hive.gateway.actions import TICKET_FIELDS, Outcome, new_request_id
from hive.gateway.cards import parse_card
from hive.gateway.chat import ChatView
from hive.gateway.desk import (
    NO_PROJECT,
    BacklogGroup,
    Crew,
    Desk,
    Glance,
    NeedsYou,
    Project,
    Row,
    glance,
)
from hive.gateway.quota import STALE_AFTER_S, Quota
from hive.gateway.reviews import Review
from hive.gateway.settings import DEFAULT_BOARD_URL, DEFAULT_TZ
from hive.gateway.snapshot import Snapshot
from hive.gateway.wake import FirstmateStatus
from hive.gateway.watchdog import View

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
--action:#8c6418;--action-ink:#fff;--honey:#e1ac10;--honey-ink:#2a1d04;--you-dot:#b8860b;
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
--action:#c9a45a;--action-ink:#0c0a08;--honey:#f0b429;--honey-ink:#1a1206;--you-dot:#f0b429;
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
border-radius:2px 2px 0 0;background:var(--honey)}
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
.pn__b{flex:1 1 auto;min-height:0;overflow-y:auto;overflow-x:hidden;padding:14px 16px 16px;overscroll-behavior:contain;
-webkit-overflow-scrolling:touch;scrollbar-width:thin;scrollbar-color:var(--line2) transparent}
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
.r__p{font:13px var(--fm);color:var(--muted);white-space:nowrap}
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
.pc__name{font-size:15px;font-weight:600;line-height:1.35;overflow-wrap:break-word}
.pc__n small{font-size:13px;color:var(--muted);overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.pc__mae{white-space:nowrap}
.aka{margin-left:6px;font:400 12px var(--fm);color:var(--muted);white-space:nowrap}
.pc__prog{display:flex;align-items:center;gap:6px;font:13px var(--fm);color:var(--muted)}
.pc--blocked .bar i{background:var(--down)}
@container pn (max-width:330px){.pc{grid-template-columns:minmax(0,1fr) auto auto}.pc__prog .bar{display:none}}
/* the review list: open Lavish sessions; the list scrolls and "+N more" stays under it */
.rvs{flex:1 1 auto;min-height:0;display:flex;flex-direction:column}
.rv__box{flex:1 1 auto;min-height:0;display:flex;flex-direction:column}
.rv__list{flex:0 1 auto;min-height:0;overflow-y:auto;overflow-x:hidden;padding:6px 16px;overscroll-behavior:contain;
-webkit-overflow-scrolling:touch;scrollbar-width:thin;scrollbar-color:var(--line2) transparent}
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
.rv__done{flex:none;padding:1px 7px;border-radius:var(--r1);background:var(--surface);color:var(--ink);font:12px var(--fm);white-space:nowrap}
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
.ar__cmd{grid-column:1;min-width:0;font:12px/1.4 var(--fm);color:var(--muted);overflow-wrap:anywhere}
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
for(var j=0;j<its.length;j++)if(its[j].getAttribute('data-item')===id)hit=its[j];
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
(it===lastPick.it||pos===lastPick.pos)){lastPick=null;location.href=it.getAttribute('data-open')||g.getAttribute('data-open');return;}
lastPick={k:k,it:it,pos:pos,t:now};picks[k]=it.getAttribute('data-item');
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

BOARD_RE = re.compile(r"https?://(?:127\.0\.0\.1|localhost|\[::1\]):4387/(session/[A-Za-z0-9_-]+)")


def _safe_url(url: str | None) -> str | None:
    if url and urlsplit(url).scheme in ("http", "https"):
        return url
    return None


class Ctx:
    """Per-render write context: CSRF token, whether writes are allowed, display settings."""

    def __init__(
        self,
        csrf: str,
        writable: bool = True,
        nxt: str = "/",
        board_url: str = DEFAULT_BOARD_URL,
        tz: str = DEFAULT_TZ,
        quota: Quota | None = None,
        descriptions: dict[str, str] | None = None,
        repos: dict[str, str] | None = None,
        can_close_reviews: bool = False,
        project_notes: dict[str, str] | None = None,
        firstmate: FirstmateStatus | None = None,
        wake_enabled: bool = False,
        watchdog: View | None = None,
        postures: fmconfig.Projects | None = None,
        terminal: bool = False,
    ) -> None:
        self.terminal = terminal  # the Terminal page is on: the firstmate chip links to it
        self.postures = postures  # firstmate's project registry; None: not read
        self.can_close_reviews = can_close_reviews  # lavish-axi is available to end a session
        self.csrf = csrf
        self.writable = writable
        self.nxt = nxt
        self.board_url = board_url.rstrip("/")
        self.tz = tz
        self.quota = quota  # None: quota-axi did not answer
        self.descriptions = descriptions or {}  # "project/item" -> cached agent description
        self.project_notes = project_notes or {}  # registry name -> one-line project description
        self.repos = repos or {}  # registry name -> GitHub repo name (when it differs)
        self.firstmate = firstmate  # cached liveness; None: not read yet
        self.watchdog = watchdog  # automatic recovery: switch, ladder, latest events
        self.wake_enabled = wake_enabled  # the wake form is shown (the action may still refuse)

    def show(self, name: str) -> str:
        """The project's display name: its GitHub repo name, else the registry name."""
        return self.repos.get(name) or name

    def name_html(self, name: str) -> str:
        """Display name, plus the local registry name as a small secondary label if it differs."""
        shown = self.show(name)
        aka = (
            f"<small class=aka title='local clone name'>{esc(name)}</small>"
            if shown != name
            else ""
        )
        # a long repo name wraps only between its words, never mid-word
        return re.sub(r"([_-])", r"\1<wbr>", esc(shown)) + aka

    def form(
        self, action: str, inner: str, cls: str = "", always: bool = False, **hidden: str
    ) -> str:
        if not self.writable and not always:
            return ""
        fields = {"csrf": self.csrf, "next": self.nxt, **hidden}
        hid = "".join(
            f'<input type=hidden name={esc(k)} value="{esc(v)}">' for k, v in fields.items()
        )
        klass = f" class='{esc(cls)}'" if cls else ""
        return f"<form method=post action='/act/{esc(action)}'{klass}>{hid}{inner}</form>"

    def link(self, url: str | None) -> str | None:
        """An http(s) URL, with loopback Lavish board links pointed at the tailnet origin."""
        safe = _safe_url(url)
        return BOARD_RE.sub(lambda m: f"{self.board_url}/{m[1]}", safe) if safe else None

    def plain(self, value: str) -> str:
        """Escaped text with loopback board URLs shortened to 'board' (for inside a link)."""
        return esc(BOARD_RE.sub("board", value))

    def boards(self, value: str) -> list[str]:
        """The tailnet URL of every loopback board link in ``value``."""
        return [f"{self.board_url}/{m[1]}" for m in BOARD_RE.finditer(value)]

    def text(self, value: str) -> str:
        """Escaped text where loopback board URLs become 'Open board' links."""
        out, last = [], 0
        for m in BOARD_RE.finditer(value):
            out.append(esc(value[last : m.start()]))
            href = esc(f"{self.board_url}/{m[1]}")
            out.append(f'<a href="{href}" rel="noopener noreferrer">Open board</a>')
            last = m.end()
        out.append(esc(value[last:]))
        return "".join(out)

    def time(self, iso: str | None) -> str:
        """A ``<time>`` the page script rewrites to the viewer's zone; server text is the fallback."""
        if not iso:
            return ""
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        except ValueError:
            return esc(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        try:
            local = dt.astimezone(ZoneInfo(self.tz))
        except Exception:  # unknown zone name or no tz database
            local = dt
        shown = f"{local:%a} {local.day} {local:%b}, {local.hour % 12 or 12}:{local:%M} {local:%p}"
        return f"<time datetime='{esc(dt.isoformat())}'>{esc(shown)}</time>"

    def date(self, epoch: float | None) -> str:
        """A full date with its year ("21 Nov 2026, 4:51 PM"), for dates that may be far off.

        The page script re-renders it in the viewer's zone, like ``time``.
        """
        iso = _iso(epoch)
        if not iso:
            return ""
        local = datetime.fromisoformat(iso)
        try:
            local = local.astimezone(ZoneInfo(self.tz))
        except Exception:  # unknown zone name or no tz database
            pass
        shown = f"{local.day} {local:%b %Y}, {local.hour % 12 or 12}:{local:%M} {local:%p}"
        return f"<time class=date datetime='{esc(iso)}'>{esc(shown)}</time>"


def _resets(at: datetime | None, tz: str, now: datetime) -> str:
    """The reset as a ``<time>`` the page script shows in the viewer's zone; ``tz`` is the fallback."""
    if at is None:
        return "not started"
    if at <= now:
        return "now"
    try:
        zone = ZoneInfo(tz)
    except Exception:  # unknown zone name or no tz database
        zone = UTC
    local, today = at.astimezone(zone), now.astimezone(zone)
    clock = f"{local.hour % 12 or 12}:{local:%M} {local:%p}".lower()
    shown = clock if local.date() == today.date() else f"{local:%a} {clock}"
    return f"resets <time class=reset datetime='{esc(at.isoformat())}'>{esc(shown)}</time>"


def _age(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    return f"{minutes} min ago" if minutes < 60 else f"{minutes // 60} h ago"


def _chip(ctx: Ctx, now: datetime | None = None) -> str:
    """The ambient quota chip: busiest window as a percent used; tap shows both windows."""
    now = now or datetime.now(UTC)
    q = ctx.quota.current(now) if ctx.quota else None
    if q is None:
        chip = (
            "<summary class='qchip qchip--unknown' aria-label='Plan quota unknown'>"
            "<span class=qchip__bar><i style='width:0%'></i></span>—</summary>"
        )
        rows = "<div><span>Plan quota unknown</span></div>"
    else:
        worst = q.worst
        chip = (
            f"<summary class='qchip qchip--{q.level}' "
            f"aria-label='Plan quota, busiest window {worst.used} percent used'>"
            f"<span class=qchip__bar><i style='width:{worst.used}%'></i></span><span>{worst.used}%"
            "<span class=tchip__l> used</span></span></summary>"
        )
        rows = "".join(
            f"<div><span>{esc(w.label)}</span><b>{w.used}% used · {_resets(w.resets_at, ctx.tz, now)}"
            "</b></div>"
            for w in q.windows
        )
        if q.as_of is not None and (now - q.as_of).total_seconds() > STALE_AFTER_S:
            rows += (
                "<div class=qpop__note><span>Claude Code figures from "
                f"<time datetime='{esc(q.as_of.isoformat())}'>{_age(now - q.as_of)}</time>"
                "</span></div>"
            )
    return (
        f"<details class=qwrap id=qchip>{chip}<div class=qpop><h3>Plan quota</h3>{rows}"
        "<div class=qpop__note><span>account-wide · shared with your own use</span></div>"
        "<section class=qpop__alerts aria-labelledby=qpop-alerts-h>"
        "<h3 id=qpop-alerts-h>Get alerts</h3>"
        "<p>On iPhone or iPad (iOS 16.4 and up):</p>"
        "<ol><li>Tap Share in Safari.</li><li>Tap Add to Home Screen.</li>"
        "<li>Open the desk from its Home Screen icon, then tap Alerts.</li></ol>"
        "<p id=alerts-note role=status></p></section>"
        "</div></details>"
    )


def _watchdog_panel(ctx: Ctx) -> str:
    """Automatic recovery: its switch (owner-only, like Wake), where the ladder stands and the
    latest events. Shown even while the snapshot is unreadable, like the wake forms."""
    wd = ctx.watchdog
    if wd is None or not ctx.wake_enabled:
        return ""
    if wd.enabled:
        button = (
            "<button name=to value=off class='btn btn--s btn--sm' type=submit>Turn off</button>"
        )
    else:
        button = "<button name=to value=on class='btn btn--s btn--sm' type=submit>Turn on</button>"
    form = ctx.form("watchdog", button, always=True)
    state = f"Automatic recovery: {esc(wd.label)}"
    if wd.config_error:
        state += f" · {esc(wd.config_error)}"
    elif wd.halted:
        state += f" · {esc(wd.halted_reason)}"
    elif wd.enabled and wd.stage:
        state += f" · rung {wd.stage} of 2 taken"
    rows = "".join(
        f"<li><time datetime='{esc(_iso(e.get('at')))}'>{esc(_clock_hm(e.get('at'), ctx.tz))}"
        f"</time> {esc(str(e.get('text', '')))}</li>"
        for e in wd.events
    )
    log = f"<ul class=wdlog>{rows}</ul>" if rows else ""
    return f"<section class=wdpanel><p>{state}</p>{form}{log}</section>"


def _iso(value: object) -> str:
    try:
        return datetime.fromtimestamp(float(value), UTC).isoformat()  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _clock_hm(value: object, tz: str) -> str:
    try:
        when = datetime.fromtimestamp(float(value), UTC)  # type: ignore[arg-type]
        try:
            when = when.astimezone(ZoneInfo(tz))
        except (ZoneInfoNotFoundError, ValueError):
            pass
        return when.strftime("%H:%M")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


_FM_DOT = {"alive": "ok", "no-beat": "warn", "down": "hot"}


def _fm_chip(ctx: Ctx) -> str:
    """Firstmate liveness from the cache, with Wake when it is down and Restart session when
    it runs with a silent watcher.

    The forms are shown even while the snapshot is unreadable (``always``): that is exactly
    when firstmate may be down. The action still re-checks and confirms before it runs.
    """
    fm = ctx.firstmate
    state = fm.state if fm else "unknown"
    label = fm.label if fm else "checking"
    detail = esc(fm.describe()) if fm else "Firstmate status has not been read yet."
    wake = ""
    if ctx.wake_enabled and fm is not None and fm.can_wake:
        wake = ctx.form(
            "wake",
            "<button class='btn btn--p' type=submit>Wake firstmate</button>",
            always=True,
        )
    elif ctx.wake_enabled and fm is not None and fm.can_restart:
        wake = ctx.form(
            "restart",
            "<button class='btn btn--p' type=submit>Restart session</button>",
            always=True,
        )
    if ctx.terminal:
        wake += (
            "<p class=fmpop__term><a class=lnk href='/terminal'>Open firstmate’s terminal</a></p>"
        )
    wake += _watchdog_panel(ctx)
    cls = _FM_DOT.get(state, "unknown")
    return (
        "<details class=qwrap id=fmchip>"
        f"<summary class='qchip qchip--{cls}' "
        f"aria-label='Firstmate {esc(label)}'>"
        f"<span class=dot></span><span><span class=tchip__l>firstmate · </span>{esc(label)}</span></summary>"
        f"<div class='qpop fmpop'><h3><span class='dot dot--{cls}'></span>Firstmate · {esc(label)}</h3>"
        f"<p>{detail}</p>{wake}</div></details>"
    )


# The hive mark (three cells) and the faint honeycomb behind every page, drawn inline: the CSP
# allows no image but the desk's own files, and an inline SVG is not an image fetch.
MARK = (
    "<svg viewBox='0 0 24 24' aria-hidden=true focusable=false><path d='M6.63 1.5 11.74 4.45v5.9"
    "L6.63 13.3 1.52 10.35v-5.9ZM17.37 1.5l5.11 2.95v5.9L17.37 13.3l-5.11-2.95v-5.9ZM12 10.8l5.11 "
    "2.95v5.9L12 22.6l-5.11-2.95v-5.9Z'/></svg>"
)
COMB = (
    "<svg class=comb aria-hidden=true focusable=false><defs><pattern id=comb width=46.77 height=81 "
    "patternUnits=userSpaceOnUse viewBox='0 0 31.18 54'><path fill=none stroke=currentColor "
    "d='M0-18 15.59-9v18L0 18-15.59 9V-9ZM31.18-18 46.77-9v18L31.18 18 15.59 9V-9ZM15.59 9 31.18 18v18"
    "L15.59 45 0 36V18ZM0 36l15.59 9v18L0 72-15.59 63V45ZM31.18 36l15.59 9v18L31.18 72 15.59 63V45Z'/>"
    "</pattern></defs><rect width=100% height=100% fill=url(#comb) /></svg>"
)

# The top bar's links, the same on every page: (``active`` name, href, label).
NAV = (
    ("desk", "/", "Desk"),
    ("chat", "/chat", "Chat"),
    ("config", "/config", "Config"),
    ("machine", "/machine", "Machine"),
    ("run", "/run", "Run"),
    ("terminal", "/terminal", "Terminal"),
    ("signins", "/signins", "Sign-ins"),
)


def _nav(active: str) -> str:
    links = "".join(
        f"<a href='{href}'{' aria-current=page' if name == active else ''}>{label}</a>"
        for name, href, label in NAV
    )
    return f"<nav aria-label=Pages>{links}</nav>"


def _page(
    title: str,
    body: str,
    active: str = "",
    attrs: str = "",
    ctx: Ctx | None = None,
    css: str = "",
    tail: str = "",
) -> str:
    chips = (_fm_chip(ctx) + _chip(ctx)) if ctx is not None else ""
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1,viewport-fit=cover'>"
        "<meta name=color-scheme content='light dark'>"
        # the phone's status bar takes the top bar's colour, in each theme
        "<meta name=theme-color media='(prefers-color-scheme: light)' content='#fffcf6'>"
        "<meta name=theme-color media='(prefers-color-scheme: dark)' content='#181512'>"
        "<meta name=mobile-web-app-capable content=yes>"
        "<meta name=apple-mobile-web-app-capable content=yes>"
        "<meta name=apple-mobile-web-app-title content='Hive desk'>"
        "<link rel=manifest href='/manifest.webmanifest'>"
        "<link rel=icon href='/icons/icon-192.png'>"
        "<link rel=apple-touch-icon href='/icons/apple-touch-icon-180.png'>"
        f"<title>{esc(title)}</title><style>{CSS}{css}</style></head>"
        f"<body{attrs}{_BODY_CLASS.get(active, '')}>{COMB}<header class=chrome>"
        f"<a class=chrome__brand href='/' aria-label='Hive desk'>{MARK}<span>hive</span></a>"
        f"{_nav(active)}<div class=chrome__st>{chips}"
        "<button id=alerts class=qchip hidden type=button>Alerts</button></div>"
        f"</header><main>{body}</main><script>{SCRIPT}</script>{tail}</body></html>"
    )


_BODY_CLASS = {"desk": " class=wide", "chat": " class=chatpage", "terminal": " class=termpage"}


def _banner(snap: Snapshot, ctx: Ctx) -> str:
    stamp = f" Snapshot {ctx.time(snap.generated)}." if snap.generated else ""
    return (
        "<div class=banner role=status><strong>Read-only fallback.</strong> "
        f"{esc(snap.reason or 'No data.')}{stamp} Nothing here can be trusted until "
        "the desk and firstmate agree on the snapshot schema"
        f"{' (saw ' + esc(snap.schema) + ')' if snap.schema else ''}.</div>"
    )


def _sc(kind: str, word: str) -> str:
    """A state chip: a small hex in the state's colour and its word (never colour alone)."""
    return f"<span class='sc sc--{kind}'><i></i>{esc(word)}</span>"


def _tabs(kind: str, items: list[tuple[str, str, int | str | None]], current: str) -> str:
    """Phone (``p``) or right-column (``r``) tabs over a page's panels; the page script swaps
    which panel shows. The server marks the first panel shown."""
    buttons = "".join(
        f"<button type=button role=tab data-tab={key} "
        f"aria-selected={'true' if key == current else 'false'}>{esc(label)}"
        + (f" <small{' data-rvn' if key == 'reviews' else ''}>{n}</small>" if n is not None else "")
        + "</button>"
        for key, label, n in items
    )
    return f"<div class='tabs tabs--{kind}' role=tablist data-tabset={kind}>{buttons}</div>"


def _decision(text: str, ctx: Ctx) -> tuple[str, str, str]:
    """A decision text as card parts: its question (HTML), its options as big buttons with the
    recommended one marked (a tap fills the reply), and the full wording behind "Full text"."""
    card = parse_card(text)
    if not card.question:
        return "", "", ""
    opts = ""
    if card.options:
        opts = (
            "<div class=opts role=group aria-label='Answer options'>"
            + "".join(
                f"<button type=button class='opt{' is-rec' if o.recommended else ''}' "
                f"data-pick='{esc(o.letter)}' aria-pressed=false><b>{esc(o.letter)}</b>"
                f"<span>{ctx.plain(o.label)}"
                + ("<em>Recommended</em>" if o.recommended else "")
                + "</span></button>"
                for o in card.options
            )
            + "</div>"
        )
    full = (
        "<details class=nyc__full><summary class=lnk>Full text</summary>"
        f"<p>{ctx.plain(card.more)}</p></details>"
        if card.more
        else ""
    )
    return ctx.plain(card.question), opts, full


_NEED_LABEL = {"hold": "Question on hold", "decision": "Decision", "merge": "Merge approval"}
# Needs-you kinds: (row class, chip kind, chip word).
_WAIT = {
    "decision": ("nyi--decision", "you", "Decision"),
    "hold": ("nyi--gate", "you", "Waiting on you"),
    "merge": ("nyi--mode", "ok", "Merge"),
}


def _posture_tag(project: str, ctx: Ctx) -> str:
    """Firstmate's merge posture for the card's project, read-only; ``unknown`` says why."""
    if ctx.postures is None:
        return ""
    label = fmconfig.posture_label(ctx.postures, project)
    return f"Merge posture: {esc(label)} · <a href='/config'>config</a>"


def _links(links: list[tuple[str, str]], full: str = "") -> str:
    out = "".join(
        f'<a class=lnk href="{esc(url)}" rel="noopener noreferrer">{label}</a>'
        for url, label in links
    )
    return f"<div class=nyc__links>{out}{full}</div>" if out or full else ""


def _lines(n: NeedsYou, lead: str, ctx: Ctx) -> tuple[str, str, str, str]:
    """A needs-you item's card lines: (what happened, what it means, option buttons, full text).
    ``lead`` is the item's own title, if any; the question then says what it means."""
    question, opts, full = _decision(n.text, ctx)
    if lead and n.text and lead != n.text:
        what, mean = ctx.plain(lead), question
    else:
        what, mean = question or ctx.plain(lead or n.ref), ""
    posture = _posture_tag(n.project, ctx) if n.kind == "merge" else ""
    mean = "".join(f"<p class=nyc__mean>{m}</p>" for m in (mean, posture) if m)
    return what, mean, opts, full


def _need(n: NeedsYou, show_project: bool, ctx: Ctx) -> str:
    """A project page's decision card: what happened, what it means, the options and the answer
    form (the page's full form, with its release choice)."""
    links = [(b, "Open board ↗") for b in ctx.boards(n.text)]
    url = ctx.link(n.url)
    if url:
        links.append((url, "Open PR ↗"))
    title = n.title or n.ref.partition("/")[0]
    where = n.project if show_project and not title.lower().startswith(n.project.lower()) else ""
    lead = " · ".join(p for p in (where, title) if p)
    act = ""
    if n.kind == "hold":
        checked = " checked" if n.gated else ""
        act = ctx.form(
            "answer",
            "<label>Your answer</label><textarea name=text required maxlength=512></textarea>"
            f"<label><input type=checkbox name=release value=1{checked}> "
            "Release the work item (resume it) instead of closing the question</label>"
            "<button class='btn btn--p'>Record answer</button>",
            "nyc__form",
            task=n.ref,
            rid=new_request_id(),
        )
    elif n.kind == "decision":
        task, _, key = n.ref.partition("/")
        act = ctx.form(
            "decision",
            "<label>Your answer (sent to the first mate)</label>"
            "<textarea name=text required maxlength=4000></textarea>"
            "<button class='btn btn--p'>Send answer</button>",
            "nyc__form",
            task=task,
            key=key,
            rid=new_request_id(),
        )
    elif n.kind == "merge":
        act = ctx.form(
            "merge",
            "<button class='btn btn--p'>Give merge word…</button>",
            "nyc__form",
            task=n.ref,
            rid=new_request_id(),
        )
    what, mean, opts, full = _lines(n, lead, ctx)
    _, kind, word = _WAIT.get(n.kind, ("", "you", n.kind))
    return (
        f"<article class='need nyc'><div class=nyc__head>{_sc(kind, word)}"
        f"<span class=nyc__where>{esc(_NEED_LABEL.get(n.kind, n.kind))}"
        f"<code>{esc(n.ref)}</code></span></div><div class=nyc__body>"
        f"<p class=nyc__what>{what}</p>{mean}{opts}{act}{_links(links, full)}</div></article>"
    )


_QUEUED_SHOWN = 4  # queued tickets listed per project before "+N more"
_ROW_ORDER = {"Held": 0, "Blocked": 1, "Queued": 2}


def _queue_state(r: Row, ctx: Ctx) -> tuple[str, str, str, str]:
    """A queued ticket's (row class, chip kind, chip word, why it waits)."""
    if r.hold:
        return "nyi--gate", "unk", "Held", f"Held: {ctx.plain(r.hold)}"
    if r.blocked_by:
        why = "Blocked by " + ", ".join(esc(b) for b in r.blocked_by)
        return "nyi--blocked", "down", "Blocked", why
    return "nyi--queued", "q", "Queued", "Queued, nothing blocking it"


def _nyx(
    *,
    project: str,
    item_id: str,
    title: str,
    cls: str,
    head: str,
    more: tuple[str, str],
    primary: bool,
    ctx: Ctx,
) -> str:
    """One Needs-you item: a shut card or row whose head opens it in place (``is-primary``).
    The page script makes a picked item its group's open one; a double tap opens the project.
    Without the script the head is a plain link to the project. ``more`` is the open item's
    body, before and after the agent-written description."""
    card = cls.startswith("nyc ")
    head_cls = "'nyx__head nyc__head'" if card else "nyx__head"
    body_cls = "'nyx__more nyc__body'" if card else "nyx__more"
    href = "/p/" + quote(project, safe="")
    text = ctx.descriptions.get(f"{project}/{item_id}")
    desc_url = "/describe?" + urlencode({"p": project, "id": item_id})
    desc = (
        "<p class=nyx__desc data-ready=1 tabindex=0 role=button aria-haspopup=dialog>"
        f"{esc(text)}</p>"
        if text
        else "<p class=nyx__desc hidden></p>"
    )
    state = " is-primary" if primary else ""
    return (
        f"<div class='nyx {cls}{state}' data-item='{esc(item_id)}' data-desc='{esc(desc_url)}' "
        f"data-open='{esc(href)}'><a class={head_cls} href='{esc(href)}' "
        f"title='{esc(title or item_id)}'>{head}</a>"
        f"<div class={body_cls}>{more[0]}{desc}{more[1]}</div></div>"
    )


def _wait_card(n: NeedsYou, ctx: Ctx, primary: bool) -> str:
    """A captain hold as a decision card: line 1 what happened, line 2 what it means, then big
    option buttons (recommended marked; a tap fills the reply), the reply and its two answer
    buttons (the one the hold asks for first), and the links."""
    cls, kind, word = _WAIT["hold"]
    ref = n.ref.partition("/")[0]
    entity = f"{ctx.show(n.project)} · {ref}"
    what, mean, opts, full = _lines(n, n.title, ctx)
    links = [(url, "Open board ↗") for url in ctx.boards(n.text)]
    pr = ctx.link(n.url)
    if pr:
        links.append((pr, "Open PR ↗"))
    links.append(("/p/" + quote(n.project, safe=""), "Open project ↗"))
    resume = "<button class='btn btn--{}' name=release value=1>Answer &amp; resume</button>"
    close = "<button class='btn btn--{}' name=release value=0>Answer &amp; close</button>"
    buttons = (
        resume.format("p") + close.format("s")
        if n.gated
        else close.format("p") + resume.format("s")
    )
    act = ctx.form(
        "answer",
        "<input type=text class=nyi__reply name=text required maxlength=512 "
        f"placeholder='Reply to {esc(entity)}…' aria-label='Reply to {esc(entity)}'>{buttons}",
        "nyi__actions nyc__act",
        task=n.ref,
        rid=new_request_id(),
    )
    head = (
        f"{_sc(kind, word)}<span class=nyc__where>{esc(ctx.show(n.project))}"
        f"<code class=nyx__id>{esc(ref)}</code></span><span class=nyc__chev>Open</span>"
        f"<span class=nyc__what>{what}</span>"
    )
    return _nyx(
        project=n.project,
        item_id=n.ref,
        title=n.title or n.text,
        cls=f"nyc {cls}",
        head=head,
        more=(mean + opts, act + _links(links, full)),
        primary=primary,
        ctx=ctx,
    )


def _queue_row(r: Row, project: str, ctx: Ctx) -> str:
    """A held, blocked or queued ticket: its chip, title and project on one line; opened, why
    it waits and the agent-written description."""
    cls, kind, word, why = _queue_state(r, ctx)
    href = "/p/" + quote(project, safe="")
    head = (
        f"{_sc(kind, word)}<span class=r__t>{esc(r.title or r.id)}</span>"
        f"<span class=r__p>{esc(ctx.show(project))}</span>"
    )
    return _nyx(
        project=project,
        item_id=r.id,
        title=r.title,
        cls=f"nyr {cls}",
        head=head,
        more=(f"<p class=nyr__why>{why}</p>", _links([(href, "Open project ↗")])),
        primary=False,
        ctx=ctx,
    )


def _sheet(sid: str, title: str, body: str, close: str, head: str = "") -> str:
    """A modal sheet (``<dialog>``) the page script opens from any ``data-sheet-open=<sid>``
    and closes on its X, a tap outside or Escape. ``title`` and ``body`` are HTML."""
    return (
        f"<dialog id={sid} class=sheet aria-labelledby={sid}-title data-sheet>"
        f"<div class=sheet__head><h2 id={sid}-title data-sheet-title>{title}</h2>{head}"
        f"<button type=button class=sheet__x data-sheet-close aria-label='{esc(close)}'>×</button>"
        f"</div>{body}</dialog>"
    )


def _open_project(href: str) -> str:
    return f"<a class='btn btn--s btn--sm' href='{esc(href)}'>Open project ↗</a>"


def _sheet_row(href: str, item_id: str, title: str, chip: tuple[str, str]) -> str:
    return (
        f"<a class=rv href='{esc(href)}'><span class=rv__title>{esc(title or item_id)}</span>"
        f"<span class=rv__meta>{_sc(*chip)}<span class=rv__proj>{esc(item_id)}</span></span></a>"
    )


def _items_list(g: BacklogGroup | None, ctx: Ctx) -> str:
    """Every item of a project's Needs-you slice (held first, then queued), for a sheet."""
    if g is None or not g.count:
        return "<p class=sheet__empty>Nothing waiting on you or queued.</p>"
    href = "/p/" + quote(g.project, safe="")
    rows = [
        _sheet_row(href, n.ref, n.title or n.text, _WAIT.get(n.kind, ("", "you", n.kind))[1:])
        for n in g.waiting
    ]
    rows += [_sheet_row(href, r.id, r.title, _queue_state(r, ctx)[1:3]) for r in g.queued]
    return f"<div class=sheet__list>{''.join(rows)}</div>"


def _lane(desk: Desk, ctx: Ctx) -> str:
    """ "Needs you": what waits on the owner, as decision cards (the first open), then every
    held, blocked or queued ticket across the projects, as rows (the first few per project;
    "+N more" opens that project's whole slice in a sheet)."""
    waiting = [n for g in desk.backlog for n in g.waiting]
    queued = sum(len(g.queued) for g in desk.backlog)
    hexn = "hexn" if waiting else "hexn hexn--q"
    meta = f"<span class=meta>{len(waiting)} waiting · {queued} held or queued</span>"
    head = (
        f"<div class=pn__h><h2>Needs you</h2><span class='{hexn} nyl__count'>"
        f"{desk.backlog_count}</span>{meta if desk.backlog else ''}</div>"
    )
    if not desk.backlog:
        n = desk.loops_running
        return (
            f"<section class='pn nyl nyl--calm is-on' data-tp=needs>{head}"
            "<div class='pn__b nyl__body'><div class='nyc nyc--calm'><div class=nyc__head>"
            f"{_sc('ok', 'All clear')}</div><div class=nyc__body>"
            "<p class=nyc__what>Nothing waits on you.</p>"
            f"<p class=nyc__mean>Backlog is empty · {n} loop{'' if n == 1 else 's'} running</p>"
            "</div></div></div></section>"
        )
    body = ""
    if waiting:
        cards = "".join(_wait_card(n, ctx, i == 0) for i, n in enumerate(waiting))
        body += (
            f"<p class=sl>Waiting on you · {len(waiting)}</p>"
            f"<div class='nyg nyg--cards' data-group=waiting>{cards}</div>"
        )
    if queued:
        rows: list[tuple[tuple[int, int, int], str]] = []
        more = ""
        for i, g in enumerate(desk.backlog):
            shown = g.queued[:_QUEUED_SHOWN]
            for j, r in enumerate(shown):
                rank = _ROW_ORDER[_queue_state(r, ctx)[2]]
                rows.append(((rank, i, j), _queue_row(r, g.project, ctx)))
            rest = len(g.queued) - len(shown)
            if rest:
                sid, href = f"nys-{i}", "/p/" + quote(g.project, safe="")
                more += (
                    f"<a class='nyq__more lnk' href='{esc(href)}' data-sheet-open={sid} "
                    f"aria-haspopup=dialog aria-controls={sid}>+{rest} more · "
                    f"{esc(ctx.show(g.project))}</a>"
                    + _sheet(
                        sid,
                        f"{ctx.name_html(g.project)} · {g.count}",
                        _items_list(g, ctx),
                        f"Close {ctx.show(g.project)}",
                        _open_project(href),
                    )
                )
        rows.sort(key=lambda row: row[0])
        body += (
            f"<p class=sl>Held or queued · {queued}</p>"
            f"<div class='nyg nyg--rows' data-group=queued>{''.join(h for _, h in rows)}</div>"
            + (f"<div class=nyq>{more}</div>" if more else "")
        )
    return f"<section class='pn nyl is-on' data-tp=needs>{head}<div class='pn__b nyl__body'>{body}</div></section>"


_CHIP = {"blocked": ("down", "Blocked"), "running": ("run", "Running"), "idle": ("unk", "Idle")}


def _target(p: Project, ctx: Ctx) -> tuple[str, str]:
    """(delegate-bar label, project field) for a focused card."""
    if p.name == NO_PROJECT:
        return "first mate", ""
    return f"{'second mate' if p.mate else 'first mate'} · {ctx.show(p.name)}", p.name


def _card_text(p: Project) -> tuple[Glance, str, str]:
    """A project's glance, its activity line (HTML) and its progress label, for its sheet."""
    g = glance(p)
    prog = "done" if g.total and g.done == g.total else f"{g.done} / {g.total} tasks"
    now = f"<b>{esc(g.lead)}</b> — {esc(g.now)}" if g.lead else esc(g.now)
    return g, now, prog


def _row_now(g: Glance) -> str:
    """The project row's one short line: who waits, what runs, or what is queued."""
    if g.status == "blocked":
        return g.lead
    if g.status == "running":
        return f"{g.lead} · {g.now}"
    head, _, rest = g.now.partition(" · ")
    line = rest.replace(" · ", ", ") if head == "Idle" and rest else g.now
    return line[:1].upper() + line[1:]


def _card(p: Project, selected: bool, ctx: Ctx, sid: str) -> str:
    """A project row: a tap focuses it (retargets the delegate bar); a tap on the focused row
    opens its detail sheet ``sid``. Without the script the focused row opens the project."""
    g = glance(p)
    label, field = _target(p, ctx)
    nxt = "/?focus=" + quote(p.name, safe="")
    open_href = "/p/" + quote(p.name, safe="")
    pct = round(100 * g.done / g.total) if g.total else 0
    sel = " is-selected" if selected else ""
    return (
        f"<a class='pc pc--{g.status}{sel}' href='{esc(open_href if selected else nxt)}' "
        f"data-card data-name='{esc(p.name)}' data-label='{esc(label)}' "
        f"data-project='{esc(field)}' data-next='{esc(nxt)}' data-open='{esc(open_href)}' "
        f"data-detail={sid}{' aria-current=true' if selected else ''}>"
        f"<span class=pc__n><span class=pc__name>{ctx.name_html(p.name)}</span>"
        f"<small><span class=pc__mae>{'second mate' if p.mate else 'first mate'}</span> · "
        f"{esc(_row_now(g))}</small></span>{_sc(*_CHIP[g.status])}"
        f"<span class=pc__prog><span class=bar><i style='width:{pct}%'></i></span>"
        f"{g.done}/{g.total}</span><span class='pc__open sr'>, tap for details</span></a>"
    )


def _card_sheet(p: Project, group: BacklogGroup | None, ctx: Ctx, sid: str) -> str:
    """The focused project's detail: status, activity, progress and its Needs-you items."""
    g, now, prog = _card_text(p)
    meta = (
        f"<p class=sheet__meta><b>{g.status}</b> · {'second mate' if p.mate else 'first mate'}"
        f" · {prog}<br>{now}</p>"
    )
    note = ctx.project_notes.get(p.name)
    about = f"<p class=sheet__about>{esc(note)}</p>" if note else ""
    return _sheet(
        sid,
        ctx.name_html(p.name),
        about + meta + _items_list(group, ctx),
        f"Close {ctx.show(p.name)}",
        _open_project("/p/" + quote(p.name, safe="")),
    )


def _dbar(focus: Project | None, ctx: Ctx, stamp: str = "") -> str:
    """The delegate bar along the bottom: who it sends to, the goal, Delegate, and when the
    desk last read firstmate. Tapping a project row retargets it (page script)."""
    label, field = _target(focus, ctx) if focus else ("first mate", "")
    nxt = "/?focus=" + quote(focus.name, safe="") if focus else "/"
    form = ctx.form(
        "delegate",
        f"<span class=dbar__target data-target>→ {esc(label)}</span>"
        "<input type=text class=dbar__in name=text required maxlength=4000 placeholder='Describe a goal…' "
        "aria-label='Delegate a goal'><button class='dbar__go btn btn--p'>Delegate</button>",
        "dbar",
        rid=new_request_id(),
        project=field,
        next=nxt,
    )
    if not form:
        return f"<div class=dbar-wrap><div class=dbar__row>{stamp}</div></div>" if stamp else ""
    hidden = " hidden" if field else ""
    return (
        "<div class=dbar-wrap><p class=dbar-flash data-flash role=status hidden></p>"
        f"<p class='dbar-note sr'>Delegate to <b data-tname>{esc(label)}</b>"
        f"<span data-nofocus{hidden}> · no project focus</span>"
        f" · tapping a project card retargets this bar</p><div class=dbar__row>{form}{stamp}</div></div>"
    )


_REVIEWS_SHOWN = 10
_CLOSE_BTN = "btn btn--s btn--sm rv__x"


def _review_form(action: str, label: str, ctx: Ctx, cls: str = _CLOSE_BTN, **hidden: str) -> str:
    """A POST form that ends review sessions. The page script turns the first tap into an
    in-place confirm ("Tap again"); without it the server shows a confirm page."""
    btn = f"<button class='{cls}' data-label='{esc(label)}'>{esc(label)}</button>"
    form = ctx.form(action, btn, rid=new_request_id(), **hidden)
    return form.replace("<form ", "<form data-rvclose ", 1) if form else ""


def _review(r: Review, ctx: Ctx) -> str:
    href = f"{ctx.board_url}/session/{r.key}"
    reply = "<span class='sc sc--run rv__reply'><i></i>Reply</span>" if r.reply else ""
    done = "<span class=rv__done>looks done</span>" if r.stale else ""
    where = f"<span class=rv__proj>{esc(ctx.show(r.project))}</span>" if r.project else ""
    close = _review_form("review-close", "Close", ctx, key=r.key) if ctx.can_close_reviews else ""
    return (
        f"<div class='rvrow{' rvrow--stale' if r.stale else ''}'>"
        f"<a class=rv href='{esc(href)}' target=_blank rel='noopener noreferrer'>"
        f"<span class=rv__title>{esc(r.title)}</span><span class=rv__meta>{reply}{where}{done}"
        f"</span></a>{close}</div>"
    )


def _reviews(reviews: list[Review], ctx: Ctx) -> str:
    """Open Lavish review sessions as rows that open on the tailnet board URL, in a panel.

    The first few are listed; "+N more" is always the list's last row and opens a sheet of
    every session (the same unread-replies-first order). Each row has a Close button; pages
    that look done (old, or beyond the newest eight) are tagged, nudged about above the list,
    and closed together by "Close all old".
    """
    if not reviews:
        return ""
    shown = "".join(_review(r, ctx) for r in reviews[:_REVIEWS_SHOWN])
    n = len(reviews)
    rest = n - _REVIEWS_SHOWN
    stale = sum(1 for r in reviews if r.stale)
    can = ctx.can_close_reviews
    more = sheet = nudge = ""
    if rest > 0:
        more = (
            "<button type=button class=rv__morebtn data-sheet-open=rvs-sheet aria-haspopup=dialog "
            f"aria-controls=rvs-sheet>+{rest} more</button>"
        )
    bulk = f"{_CLOSE_BTN} rv__x--bulk"
    if stale and can:
        word = "page looks" if stale == 1 else "pages look"
        nudge = (
            f"<div class=rvs__nudge role=status><span>{stale} {word} done: close "
            f"{'it' if stale == 1 else 'them'}?</span>"
            "<button type=button class='btn btn--s btn--sm rv__review' data-sheet-open=rvs-sheet "
            "aria-haspopup=dialog aria-controls=rvs-sheet>Review</button>"
            f"{_review_form('review-close-old', f'Close {stale}', ctx, bulk)}</div>"
        )
    if rest > 0 or (stale and can):
        sheet = _sheet(
            "rvs-sheet",
            f"Review pages · {n}",
            f"<div class=sheet__list>{''.join(_review(r, ctx) for r in reviews)}</div>",
            "Close review pages",
            _review_form("review-close-old", "Close all old", ctx, bulk) if stale and can else "",
        )
    return (
        "<section class='pn rvp' data-tp=reviews data-rp=reviews>"
        f"<div class=pn__h><h2>Review pages</h2><span class='hexn hexn--q' data-rvn>{n}</span></div>"
        f"<div class=rvs>{nudge}<div class=rv__box><div class=rv__list tabindex=0 role=region "
        f"aria-label='Open review pages'>{shown}</div>{more}</div>{sheet}</div></section>"
    )


def render_home(
    snap: Snapshot,
    desk: Desk | None,
    ctx: Ctx,
    focus: str | None = None,
    reviews: list[Review] | None = None,
) -> str:
    """The desk: Needs you, Projects and Review pages side by side over the delegate bar.
    Narrower screens tab the right-hand panels; a phone tabs all three."""
    attrs = " data-refresh=30000"
    if desk is None:
        body = f"<div class=ph><h1>Hive desk</h1></div>{_banner(snap, ctx)}"
        return _page("Hive desk", body, "desk", attrs, ctx)
    if focus not in desk.projects:
        focus = desk.default_focus()
    groups = {g.project: g for g in desk.backlog}
    cards = details = ""
    blocked = 0
    for i, p in enumerate(desk.projects.values()):
        cards += _card(p, p.name == focus, ctx, f"pcs-{i}")
        details += _card_sheet(p, groups.get(p.name), ctx, f"pcs-{i}")
        blocked += glance(p).status == "blocked"
    more = "".join(
        f"<p class=stamp>+{n} more owned by {esc(owner)}, not shown</p>"
        for owner, n in desk.more.items()
    )
    count = len(desk.projects)
    projects = (
        "<section class='pn pcsec is-ron' data-tp=projects data-rp=projects>"
        f"<div class=pn__h><h2>Projects</h2><span class='hexn hexn--q'>{count}</span>"
        + (f"<span class=meta>{blocked} blocked</span>" if blocked else "")
        + f"</div><div class='pn__b pcs'>{cards or '<p class=empty>No projects yet</p>'}{more}</div>"
        f"{details}</section>"
    )
    review = _reviews(reviews or [], ctx)
    tabs = [("needs", "Needs you", desk.backlog_count), ("projects", "Projects", count)]
    right = ""
    if review:
        tabs.append(("reviews", "Reviews", len(reviews or [])))
        right = _tabs(
            "r",
            [("projects", "Projects", count), ("reviews", "Review pages", len(reviews or []))],
            "projects",
        )
    stamp = f"<span class=stamp>Updated {ctx.time(desk.generated)}</span>" if desk.generated else ""
    body = (
        f"<div class=screen data-stack><div class='pgb desk{'' if review else ' desk--two'}' data-tabs>"
        f"{_tabs('p', tabs, 'needs')}{_lane(desk, ctx)}<div class=dcol>{right}{projects}{review}</div></div>"
        f"{_dbar(desk.projects.get(focus) if focus else None, ctx, stamp)}"
        + _sheet(
            "desc-sheet",
            "",
            "<div class=sheet__list><p class=sheet__text data-sheet-text></p></div>",
            "Close description",
        )
        + "</div>"
    )
    return _page("Hive desk", body, "desk", attrs, ctx)


# a crew's state: its chip colour; the word is the state itself
_CREW_CHIP = {"working": "run", "done": "ok", "idle": "q", "waiting": "you", "stuck": "down"}
# a backlog ticket's state, when it is not queued: (chip colour, chip word)
_ROW_CHIP = {"done": ("ok", "Done"), "in_progress": ("run", "In progress")}


def _word(state: str) -> str:
    return esc(state.replace("_", " ").capitalize())


def _crew(c: Crew, ctx: Ctx) -> str:
    controls = ctx.form(
        "control",
        "<button class='btn btn--s btn--sm' name=verb value=interrupt>Interrupt…</button>"
        "<button class='btn btn--s btn--sm' name=verb value=relaunch>Relaunch…</button>",
        "crew__ctl",
        task=c.id,
    )
    return (
        f"<li class=crew><div class=crew__top>{_sc(_CREW_CHIP.get(c.state, 'unk'), _word(c.state))}"
        f"<b>{esc(c.title or c.id)}</b>"
        f"<a class=lnk href='/w/{esc(quote(c.id, safe=''))}'>Watch live</a></div>"
        f"<p class=crew__d>{esc(c.detail)}"
        f"<span>{' · '.join([f'<code>{esc(c.id)}</code>'] + [esc(x) for x in (c.kind, c.harness) if x])}"
        "</span></p>"
        f"{controls}</li>"
    )


def _ticket_edit(r: Row, project: str, ctx: Ctx) -> str:
    if r.state == "done":
        return ""
    options = "".join(f"<option>{esc(f)}</option>" for f in TICKET_FIELDS)
    return ctx.form(
        "ticket",
        f"<details class=tk><summary>Request an edit</summary><label>Field</label>"
        f"<select name=field>{options}</select><label>New text</label>"
        "<textarea name=text required maxlength=4000></textarea>"
        "<button>Send edit request</button></details>",
        mode="edit",
        ticket=r.id,
        project=project,
        rid=new_request_id(),
    )


def _new_ticket(project: str, ctx: Ctx) -> str:
    return ctx.form(
        "ticket",
        "<details class='fold tk'><summary>New ticket</summary><label>Title</label>"
        "<input type=text name=title required maxlength=200><label>Details</label>"
        "<textarea name=text maxlength=4000></textarea>"
        "<button>Send create request</button></details>",
        mode="create",
        project=project,
        rid=new_request_id(),
    )


def _backlog_row(r: Row, titles: dict[str, str], project: str, ctx: Ctx) -> str:
    if r.state == "queued":
        _, kind, word, _ = _queue_state(r, ctx)
    else:
        kind, word = _ROW_CHIP.get(r.state, ("unk", _word(r.state)))
    extra = [f"<code>{esc(r.id)}</code>"]
    if r.owner and r.owner != "main":
        extra.append(f"owner: {esc(r.owner)}")
    if r.hold:
        extra.append(f"held: {esc(r.hold)}")
    if r.blocked_by:
        extra.append("blocked by " + esc(", ".join(titles.get(b) or b for b in r.blocked_by)))
    pr = ctx.link(r.pr_url)
    if pr:
        extra.append(f'<a href="{esc(pr)}" rel="noopener noreferrer">PR</a>')
    return (
        f"<li class='bl bl--{esc(r.state)}'>{_sc(kind, word)}<div class=bl__b>"
        f"<b class=bl__t>{esc(r.title or r.id)}</b><p class=bl__d>{' · '.join(extra)}</p>"
        f"{_ticket_edit(r, project, ctx)}</div></li>"
    )


def render_project(
    name: str,
    snap: Snapshot,
    project: Project | None,
    ctx: Ctx,
) -> str:
    """A project lens: what needs the owner beside its crews and backlog, as two panels (tabs
    on a phone), the approved board's Needs page."""
    attrs = " data-refresh=30000"
    back = "<a class='lnk ph__back' href='/'>← Desk</a>"
    if project is None:
        body = _banner(snap, ctx) if not snap.ok else "<p>Unknown project.</p>"
        return _page(name, f"<div class=ph>{back}</div>{body}", "", attrs, ctx)
    needs = (
        "".join(_need(n, False, ctx) for n in project.needs_you)
        or "<p class=empty>Nothing needs you.</p>"
    )
    titles = {r.id: r.title for r in project.rows}
    rows = "".join(_backlog_row(r, titles, project.name, ctx) for r in project.rows)
    crews = "".join(_crew(c, ctx) for c in project.crews)
    n_need, n_rows, n_crews = len(project.needs_you), len(project.rows), len(project.crews)
    return _page(
        f"{ctx.show(name)} · Hive desk",
        f"<div class=ph>{back}<h1>{ctx.name_html(name)}</h1></div><div class='pgb npg' data-tabs>"
        + _tabs("p", [("needs", "Needs you", n_need), ("work", "Work", n_rows)], "needs")
        + "<section class='pn is-on' data-tp=needs><div class=pn__h><h2>Needs you</h2>"
        + (f"<span class=hexn>{n_need}</span>" if n_need else "")
        + f"</div><div class=pn__b>{needs}</div></section>"
        "<section class=pn data-tp=work><div class=pn__h><h2>Work</h2>"
        f"<span class=meta>{n_crews} {'crew' if n_crews == 1 else 'crews'} · {n_rows} "
        f"{'ticket' if n_rows == 1 else 'tickets'}</span></div><div class=pn__b>"
        f"<p class=sl>Crews · {n_crews}</p>"
        + (f"<ul class=crews>{crews}</ul>" if crews else "<p class=meta>No live crews.</p>")
        + f"<p class=sl>Backlog · {n_rows}</p>{_new_ticket(project.name, ctx)}"
        + (f"<ul class=bls>{rows}</ul>" if rows else "<p class=meta>Empty.</p>")
        + "</div></section></div>",
        "",
        attrs,
        ctx,
    )


# a sent note's kind: (chip colour, chip word); plain chat has no chip
_KIND_LABEL = {
    "chat": ("", ""),
    "ticket": ("q", "Ticket request"),
    "merge": ("ok", "Merge word"),
    "decision": ("you", "Answer"),
    "delegate": ("run", "Delegated"),
}
_STATE_LABEL = {
    "pending": "Sent",
    "seen": "Seen",
    "replied": "Answered",
}


def _thread(view: ChatView, ctx: Ctx) -> str:
    if not view.available:
        return "<p class=mute>Messages are unavailable (firstmate inbox script did not answer).</p>"
    msgs = []
    for r in sorted(view.receipts, key=lambda r: r.at):  # oldest first, newest at the bottom
        rid = f" data-rid='{esc(r.request_id)}'" if r.request_id else ""
        if _KIND_LABEL[r.kind][1]:  # a request, not a plain note: a receipt card led by its kind
            bubble = f"<div class='bubble rc'>{_sc(*_KIND_LABEL[r.kind])}<span>{esc(r.body)}</span></div>"
        else:
            bubble = f"<div class=bubble>{esc(r.body)}</div>"
        msgs.append(
            f"<div class='msg me'{rid}>{bubble}"
            f"<span class=meta>{ctx.time(r.at)} · {esc(_STATE_LABEL[r.state])}</span></div>"
        )
        if r.reply is not None:
            msgs.append(
                "<div class='msg fm'><span class=msg__who><i></i>First mate</span>"
                f"<div class=bubble>{esc(r.reply)}</div>"
                f"<span class=meta>{ctx.time(r.reply_at)}</span></div>"
            )
    out = "".join(msgs) or "<p class='mute empty'>No messages yet. Say hello below.</p>"
    if view.omitted:
        out = "<p class=meta>Older entries omitted: " + esc("; ".join(view.omitted)) + "</p>" + out
    return out


def render_watch(crew: Crew, ctx: Ctx) -> str:
    """One crew's recent output, live: the output scrolls inside its panel."""
    return _page(
        f"{crew.id} · Hive desk",
        "<div class=ph><a class='lnk ph__back' href='/'>← Desk</a>"
        f"<h1>{esc(crew.title or crew.id)}</h1>"
        f"{_sc(_CREW_CHIP.get(crew.state, 'unk'), _word(crew.state))}"
        f"<span class=sub>{esc(crew.detail)} · <code>{esc(crew.id)}</code> · "
        f"{esc(crew.harness)}</span></div>"
        "<section class='pn watch'><div class=pn__h><h2>Recent output</h2>"
        "<span class=meta id=tail-status role=status>Loading…</span></div>"
        "<pre class='pn__b tail' id=tail aria-label='Recent output' aria-live=off></pre></section>",
        "",
        f" data-tail='{esc(crew.id)}'",
        ctx,
    )


def render_chat(view: ChatView, ctx: Ctx) -> str:
    if view.can_receive is False:
        status = (
            "<div class=banner role=status>The first mate session is not receiving right now. "
            "Notes are saved and will be read when it is back.</div>"
        )
    else:
        status = ""
    form = ctx.form(
        "chat",
        "<label for=chat-text>Message the first mate</label><textarea id=chat-text name=text "
        "rows=1 required maxlength=4000 enterkeyhint=send placeholder='Ask, delegate, or steer…'>"
        "</textarea><button class='btn btn--p'>Send</button>",
        "chatf",
        rid=new_request_id(),
    )
    return _page(
        "Chat · Hive desk",
        f"<div class=ph><h1>Chat with the first mate</h1></div>{status}<section class='pn chatpn'>"
        f"<div class='pn__b thread' id=thread aria-live=polite>{_thread(view, ctx)}</div>"
        "<button id=jump class='btn btn--s btn--sm' type=button hidden>New messages \u2193</button>"
        f"<div class=dock>{form}</div></section>",
        "chat",
        " data-poll=4000",
        ctx,
    )


def _cfg_note(error: str) -> str:
    return f"<p class=unk>unknown: {esc(error)}</p>"


def _profile_tags(p: fmconfig.Profile) -> str:
    """One worker profile as tags: harness, model and effort; a default reads as plain text."""
    tags = [f"<span class=tag>{esc(p.harness)}</span>"]
    tags.append(
        f"<span class=tag>{esc(p.model)}</span>"
        if p.model
        else "<span class=meta>harness default model</span>"
    )
    tags.append(
        f"<span class=tag>{esc(p.effort)}</span>"
        if p.effort
        else "<span class=meta>default effort</span>"
    )
    return f"<span class=ms>{''.join(tags)}</span>"


def render_config(cfg: fmconfig.FmConfig, ctx: Ctx) -> str:
    """Firstmate's merge posture and worker profiles, read-only. A source that cannot be
    read shows ``unknown`` with the reason; the page itself never fails."""
    pr = cfg.projects
    count = ""
    if pr.error:
        merge = f"<div class='pn__b'>{_cfg_note(pr.error)}</div>"
    else:
        rows = [
            f"<tr><td>{esc(n)}</td><td><span class=tag>{esc(p.mode)}</span></td>"
            f"<td>{_sc('run', 'On') if p.yolo else _sc('q', 'Off')}</td></tr>"
            for n, p in sorted(pr.postures.items())
        ] + [
            f"<tr><td>{esc(n)}</td><td colspan=2 class=unk>unknown: {esc(why)}</td></tr>"
            for n, why in sorted(pr.ambiguous.items())
        ]
        count = str(len(rows))
        merge = (
            "<div class='pn__b pn__b--flush'><table class=tbl><thead><tr><th>Project</th>"
            f"<th>Delivery</th><th>Yolo</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
        )
    d = cfg.dispatch
    rules = ""
    if d.error:
        profiles = _cfg_note(d.error)
    else:
        items = [
            f"<li><span class=prof__w title='{esc(r.when)}'>{esc(r.when)}</span>"
            f"{''.join(_profile_tags(p) for p in r.profiles)}</li>"
            for r in d.rules
        ]
        if d.default:
            items.append(
                "<li><span class=prof__w>Anything else (default)</span>"
                f"{''.join(_profile_tags(p) for p in d.default)}</li>"
            )
        rules = str(len(items))
        profiles = f"<ul class=prof>{''.join(items)}</ul>"
    if cfg.harness.absent:
        harness = "<span class=meta>not set</span>"
    elif cfg.harness.error:
        harness = _cfg_note(cfg.harness.error)
    else:
        harness = f"<span class=tag>{esc(cfg.harness.text)}</span>"
    if cfg.permission.error:
        perm = _cfg_note(cfg.permission.error)
    else:
        perm = f"<span class=tag>{esc(cfg.permission.text)}</span>"
        if cfg.permission.absent:
            perm += " <span class=meta>default</span>"
    gate = cfg.workers_ready
    ready = (
        f"{_sc('down', 'Will not start')} <span class=meta>{esc(gate)}</span>"
        if gate
        else _sc("ok", "Can start")
    )

    def hexn(n: str) -> str:
        return f"<span class='hexn hexn--q'>{n}</span>" if n else ""

    return _page(
        "Config · Hive desk",
        f"<div class=ph><h1>Config</h1>{_sc('q', 'Read-only')}"
        "<span class=sub>From firstmate on each load. Hive cannot edit it.</span></div>"
        "<div class='pgb cfg' data-tabs>"
        + _tabs(
            "p",
            [
                ("posture", "Merge posture", int(count) if count else None),
                ("workers", "Workers", None),
            ],
            "posture",
        )
        + "<section class='pn is-on' data-tp=posture><div class=pn__h><h2>Merge posture</h2>"
        f"{hexn(count)}</div>{merge}</section>"
        "<div class=cfg__col data-tp=workers>"
        f"<section class='pn cfg__prof'><div class=pn__h><h2>Worker profiles</h2>{hexn(rules)}</div>"
        f"<div class=pn__b>{profiles}</div></section>"
        "<section class=pn><div class=pn__h><h2>Worker launch</h2></div><div class=pn__b>"
        f"<dl class=kv><dt>Crew harness</dt><dd>{harness}</dd>"
        f"<dt>Claude permission mode</dt><dd>{perm}</dd>"
        f"<dt>Workers</dt><dd>{ready}</dd></dl></div></section></div></div>",
        "config",
        "",
        ctx,
    )


MACHINE_REFRESH_MS = 30000  # an open Machine page re-reads this often; each read may start a round
_MSTATE = {"ok": "OK", "warn": "Warning", "down": "Down", "unknown": "Unknown"}


def _mstate(state: str) -> str:
    return state if state in _MSTATE else "unknown"


def _moment(m: machine.Moment | None, ctx: Ctx, lead: bool = False) -> str:
    """ "expires <date>", or "Expires <date>" when it opens the line."""
    if m is None:
        return ""
    label = m.label[:1].upper() + m.label[1:] if lead else m.label
    return f"{esc(label)} {ctx.date(m.at) if m.exact else ctx.time(_iso(m.at))}"


# a state as a chip colour and as the glyph in the chain's hex (the chip carries the word)
_MCHIP = {"ok": "ok", "warn": "warn", "down": "down", "unknown": "unk"}
_MGLYPH = {
    "ok": "M3 7.4 5.8 10 11 4",
    "warn": "M7 3.5v4.2M7 10.4v.2",
    "down": "M4.2 4.2l5.6 5.6M9.8 4.2l-5.6 5.6",
    "unknown": "M5 5.3a2 2 0 1 1 2.7 1.9c-.5.2-.7.5-.7 1v.3M7 10.4v.2",
}


def _mchip(state: str, word: str = "") -> str:
    state = _mstate(state)
    return _sc(_MCHIP[state], word or _MSTATE[state])


def _mrow(state: str, urgent: bool, label: str, head: str, detail: list[str]) -> str:
    """One link of the chain: a hex mark, its label and reading, then a state chip (a word,
    not colour alone)."""
    state = _mstate(state)
    cls = f"ch ch--{state}" + (" ch--urgent" if urgent else "")
    line = " · ".join(d for d in detail if d)
    return (
        f"<li class='{cls}'><span class=ch__n><svg viewBox='0 0 14 14' aria-hidden=true>"
        f"<path d='{_MGLYPH[state]}'/></svg></span><div class=ch__b><p class=ch__l><b>{esc(label)}</b>"
        + (f" · {esc(head)}" if head else "")
        + "</p>"
        + (f"<p class=ch__d>{line}</p>" if line else "")
        + f"</div>{_mchip(state)}</li>"
    )


def _timeline(view: machine.MachineView) -> str:
    """The dated risks as marks on a line from now, the red band's days shaded."""
    now = view.checked_at
    if now is None:
        return ""
    dated = [(k, max(0.0, (k.order - now) / 86400)) for k in view.risks if k.order != float("inf")]
    if not dated:
        return ""
    span = max(30.0, max(d for _, d in dated) * 1.08)

    def x(days: float) -> str:
        return f"{min(days, span) / span * 100:.2f}%"

    marks = "".join(
        f"<span class='tl__m tl__m--{_MCHIP[_mstate(k.state)]}' style='left:{x(d)}'></span>"
        f"<span class='tl__lab tl__lab--{'dn' if i % 2 else 'up'}"
        f"{' tl__lab--first' if d < span * 0.04 else ''}' style='left:{x(d)}'>"
        f"{'Now' if d < 1 else f'{int(d)} d'}</span>"
        for i, (k, d) in enumerate(dated)
    )
    return (
        "<div class=tl role=img aria-label='Upcoming risks by days from now'>"
        f"<div class=tl__ax><span class=tl__w style='width:{x(machine.URGENT_DAYS)}'></span></div>"
        f"{marks}</div>"
    )


def render_machine(view: machine.MachineView, ctx: Ctx) -> str:
    """The chain that has to hold and the upcoming risks, read-only.

    Every row always renders: a probe that could not answer shows ``unknown`` with a fixed
    reason, so the page itself never fails. It holds no form and no button.
    """
    rows = "".join(
        _mrow(
            r.state,
            r.urgent,
            r.label,
            r.headline,
            [
                esc(r.detail),
                _moment(r.moment, ctx),
                f"<span class=ch__at>checked {ctx.time(_iso(r.checked_at))}</span>"
                if r.checked_at and (view.checked_at is None or r.checked_at < view.checked_at - 60)
                else "",  # a reading older than the round says when it was taken
            ],
        )
        for r in view.rows
    )
    risks = "".join(
        f"<li class='risk risk--{_mstate(k.state)}{' risk--urgent' if k.urgent else ''}'>"
        f"{_mchip(k.state)}<b>{esc(k.label)}</b><span class=when>{esc(k.due)}</span>"
        + (
            f"<span class=d>{line}</span>"
            if (
                line := " · ".join(
                    d for d in (_moment(k.moment, ctx, lead=True), esc(k.detail)) if d
                )
            )
            else ""
        )
        + "</li>"
        for k in view.risks
    )
    counts = {
        s: sum(_mstate(r.state) == s for r in view.rows) for s in ("down", "warn", "unknown", "ok")
    }
    summary = "".join(f"<li>{_mchip(s, f'{n} {_MSTATE[s]}')}</li>" for s, n in counts.items() if n)
    worst = next((f"{n} {_MSTATE[s]}" for s, n in counts.items() if n), "")
    stamp = (
        f"<span class=stamp>Checked {ctx.time(_iso(view.checked_at))}</span>"
        if view.checked_at
        else ""
    )
    note = esc(view.note) or "Nothing to report."
    a = view.alarm
    alarm = (
        "<p class=sl>Gone-dark alarm</p><ol class='chain chain--one'>"
        + _mrow(a.state, False, a.label, a.headline, [esc(a.detail), _moment(a.moment, ctx)])
        + "</ol>"
        if a is not None
        else ""
    )
    return _page(
        "Machine · Hive desk",
        f"<div class=ph><h1>Machine</h1><ul class=mach__sum aria-label='Chain summary'>{summary}</ul>"
        "<span class=sub>Every link has to hold to reach firstmate. Renew a sign-in on the "
        f"<a href='/signins'>Sign-ins page</a>.</span>{stamp}"
        + (
            "<a class='lnk mach__term' href='/terminal'>Open firstmate’s terminal</a>"
            if ctx.terminal
            else ""
        )
        + "</div><div class='pgb mach' data-tabs>"
        + _tabs(
            "p",
            [("chain", "The chain", worst or None), ("risks", "Upcoming risks", len(view.risks))],
            "chain",
        )
        + "<section class='pn mach__col is-on' data-tp=chain><div class=pn__h><h2>The chain</h2>"
        "<span class=meta>Read-only</span></div>"
        f"<div class=pn__b><ol class=chain>{rows}</ol>{alarm}</div></section>"
        "<section class='pn mach__col' data-tp=risks><div class=pn__h><h2>Upcoming risks</h2>"
        + (f"<span class='hexn hexn--q'>{len(view.risks)}</span>" if view.risks else "")
        + "</div><div class=pn__b>"
        + (
            f"{_timeline(view)}<ul class=risks>{risks}</ul>"
            if risks
            else f"<p class=empty>{note}</p>"
        )
        + "</div></section></div>",
        "machine",
        f" data-refresh={MACHINE_REFRESH_MS} data-machine",
        ctx,
    )


RUN_REFRESH_MS = 30000  # only while the live stream is down; a run's start and end push a refresh
_RUN_RESULT = {
    runqueue.OK: ("ok", "ok", "OK"),
    runqueue.REFUSED: ("warn", "warn", "Refused"),
    runqueue.FAILED: ("down", "down", "Failed"),
    runqueue.TIMED_OUT: ("down", "down", "Timed out"),
    runqueue.RUNNING: ("unknown", "run", "Running"),
    runqueue.UNKNOWN: ("unknown", "unknown", "Unknown"),
}


def _secs(seconds: float | None) -> str:
    """A run's duration; each number keeps its unit on the same line (a no-break space)."""
    if seconds is None:
        return ""
    if seconds < 10:
        return f"{seconds:.1f}\u00a0s"
    if seconds < 120:
        return f"{int(seconds)}\u00a0s"
    return f"{int(seconds // 60)}\u00a0min {int(seconds % 60)}\u00a0s"


def _run_card(card: runqueue.Card, ctx: Ctx) -> str:
    """One fix button: what it does, how long it takes, the exact command or helper verb."""
    a = card.action
    cls = "ar" + (" ar--down" if a.down else "") + (" ar--off" if card.why_not else "")
    # off: a disabled button; its reason is said once by the Windows note when it is the same
    form = "<button class='btn btn--s' type=button disabled>Run</button>"
    off = card.why_not if card.why_not and card.why_not != runqueue.NOT_SET_UP else ""
    if not card.why_not:
        pick = ""
        if a.choice:
            fid = f"rq-{a.key}"
            opts = "".join(
                f"<option value={v}{' selected' if v == a.default else ''}>{esc(a.short(v))}</option>"
                for v in a.choices
            )
            pick = f"<label for={fid}>For</label><select id={fid} name=n>{opts}</select>"
        btn = "btn btn--s btn--danger" if a.down else "btn btn--s"
        form = ctx.form(
            "run", f"{pick}<button class='{btn}' type=submit>Run</button>", always=True, do=a.key
        )
    return (
        f"<li class='{cls}'><div class=ar__t><b>{esc(a.label)}</b>"
        f"<span class=meta>{esc(a.took)}</span></div>{form}"
        f"<p class=ar__d title='{esc(a.plain)}'>{esc(a.plain)}</p>"
        f"<code class=ar__cmd>{esc(a.command())}</code>"
        + (f"<p class=ar__off>{esc(off)}</p>" if off else "")
        + "</li>"
    )


def _run_row(r: runqueue.Run, owner: str, ctx: Ctx) -> str:
    row, chip, word = _RUN_RESULT.get(r.result, _RUN_RESULT[runqueue.UNKNOWN])
    who = "you" if r.who == owner else machine.redact(r.who or "-", 40)
    took = _secs(r.duration_s)
    line = " · ".join(
        x
        for x in (f"by {esc(who)}", ctx.time(_iso(r.start)), esc(r.detail), took and f"took {took}")
        if x
    )
    return (
        f"<li class='runrow runrow--{row}'>{_sc(_MCHIP.get(chip, chip), word)}"
        f"<b>{esc(r.label)}</b><span class=d>{line}</span></li>"
    )


def _helper_note(state: str) -> str:
    if state == runqueue.READY:
        return (
            "<p class='meta run__lead'>Through the admin helper only: each button writes one "
            "request line for it and starts its task, and its answer is shown below.</p>"
        )
    chip, more = (
        (
            "Not answered",
            "It has not answered the desk yet. If the sitting is done, tap Check the admin "
            "helper; the other Windows buttons are offered once it answers.",
        )
        if state == runqueue.UNPROVEN
        else (
            "Not set up",
            "The one sitting at the PC sets it up. Until then no Windows button is offered.",
        )
    )
    return (
        f"<div class=note role=status>{_sc('warn', chip)}<span><b>{esc(runqueue.NOT_SET_UP)}</b> "
        f"{esc(more)} ({esc(runqueue.SITTING)})</span></div>"
    )


def render_run(view: runqueue.RunView, ctx: Ctx) -> str:
    """The Run page: fixed fix buttons, then the last runs. Nothing runs from a page load;
    each button needs a confirm step (two for the ones that take things down)."""
    linux = [c for c in view.cards if c.action.side == "linux"]
    windows = [c for c in view.cards if c.action.side == "windows"]
    fold = (
        f"<details class=fold><summary>Not offered · {len(view.not_offered)}</summary><ul>"
        + "".join(
            f"<li>{esc(label)}<span>{esc(why)}</span></li>" for label, why in view.not_offered
        )
        + "</ul></details>"
        if view.not_offered
        else ""
    )
    rows = "".join(_run_row(r, view.owner, ctx) for r in view.runs)
    runs = f"<ol class=runs>{rows}</ol>" if rows else "<p class=empty>No runs yet.</p>"
    return _page(
        "Run · Hive desk",
        "<div class=ph><h1>Run</h1><span class=sub>Fixed fixes only. Tap, check what runs, tap "
        "again. Every run is logged.</span></div><div class='pgb run' data-tabs>"
        + _tabs(
            "p",
            [
                ("linux", "Linux", len(linux)),
                ("win", "Windows", len(windows)),
                ("runs", "Runs", None),
            ],
            "linux",
        )
        + "<section class='pn is-on' data-tp=linux><div class=pn__h><h2>This desk · Linux</h2>"
        "<span class=meta>runs as you, never as root</span></div>"
        f"<div class=pn__b><ul class=ars>{''.join(_run_card(c, ctx) for c in linux)}</ul></div></section>"
        "<section class=pn data-tp=win><div class=pn__h><h2>The PC · Windows</h2></div>"
        f"<div class=pn__b>{_helper_note(view.helper)}"
        f"<ul class=ars>{''.join(_run_card(c, ctx) for c in windows)}</ul>{fold}</div></section>"
        f"<section class=pn data-tp=runs><div class=pn__h><h2>Last {runqueue.SHOWN_RUNS} runs</h2>"
        + (f"<span class='hexn hexn--q'>{len(view.runs)}</span>" if view.runs else "")
        + f"</div><div class=pn__b>{runs}</div></section></div>",
        "run",
        f" data-refresh={RUN_REFRESH_MS} data-run",
        ctx,
    )


SIGNINS_REFRESH_MS = 30000  # an open Sign-ins page re-reads its rows this often


def _signin_poll(ctx: Ctx, token: str, visible: bool) -> str:
    """The form the page asks "how is the sign-in going" with. The starting page's token goes
    in the request body, never in a link. Hidden on the page the script drives."""
    button = "<button class='btn btn--s' type=submit>Check again</button>" if visible else ""
    return (
        f"<form id=signin-poll method=post action='/act/signin-flow'{'' if visible else ' hidden'}>"
        f'<input type=hidden name=csrf value="{esc(ctx.csrf)}">'
        "<input type=hidden name=next value='/signins'>"
        f'<input type=hidden name=flow value="{esc(token)}">{button}</form>'
    )


def _time_of_day(epoch: float, ctx: Ctx) -> str:
    """A time of day ("4:10 pm") for something minutes away, in the viewer's zone."""
    at = datetime.fromtimestamp(epoch, UTC)
    try:
        local = at.astimezone(ZoneInfo(ctx.tz))
    except Exception:  # unknown zone name or no tz database
        local = at
    shown = f"{local.hour % 12 or 12}:{local:%M} {local:%p}".lower()
    return f"<time class=reset datetime='{esc(at.isoformat())}'>{esc(shown)}</time>"


def signin_flow(view: signins.FlowView, ctx: Ctx) -> str:
    """The sign-in flow as one page may see it.

    The link, the one-time code and the page's token are in it only for the page that
    started the flow (``view.mine``) and only while it waits for the owner.
    """
    v, name = view, esc(view.label)
    if v.phase == signins.IDLE:
        return ""
    if not v.active:
        cls = {signins.DONE_OK: "sgf--ok", signins.DONE_FAILED: "sgf--failed"}.get(
            v.phase, "sgf--quiet"
        )
        when = f"<span class=mute>{ctx.time(_iso(v.ended_at))}</span>" if v.ended_at else ""
        good = (
            f" Good until {ctx.date(v.expires)}."
            if v.expires and v.phase == signins.DONE_OK
            else ""
        )
        chip = {signins.DONE_OK: _sc("ok", "Done"), signins.DONE_FAILED: _sc("down", "Failed")}
        return (
            f"<div class='sgf {cls}' role=status><div class=sgf__h><p class=sgf__title>{name} "
            f"sign-in</p>{chip.get(v.phase, '')}{when}</div><p>{esc(v.message)}{good}</p></div>"
        )
    cancel = ""
    if v.phase != signins.STOPPING:
        label = "Cancel" if v.mine else "Stop that sign-in"
        cancel = ctx.form(
            "signin-cancel",
            f"<button class='btn btn--s btn--sm' type=submit>{label}</button>",
            always=True,
            flow=v.token,
        )
    stops = f"<span class=meta>Stops by itself at {_time_of_day(v.ends_at, ctx)}.</span>"
    note = f"<p class=sgf__note role=alert>{esc(v.note)}</p>" if v.note else ""
    if not v.mine:
        body = (
            "<p>Its link is shown only on the page that started it. If that page is gone, "
            "stop this sign-in and start again.</p>"
        )
    elif v.phase == signins.STARTING:
        body = f"<p role=status>Starting the {name} login command…</p>"
    elif v.phase == signins.CHECKING:
        body = f"<p role=status>Checking with {name}…</p>"
    elif v.phase == signins.STOPPING or not v.link:
        body = "<p role=status>Stopping…</p>"
    else:
        host = esc(urlsplit(v.link).hostname or "")
        steps = (
            "<li><b>Open the sign-in page and sign in as yourself.</b>"
            f'<a class=sgf__link href="{esc(v.link)}" target=_blank rel="noopener noreferrer">'
            f"Open the {name} sign-in page ↗</a><span class=sgf__host>{host}</span></li>"
        )
        if v.code:
            steps += (
                "<li><b>Enter this one-time code there.</b>"
                f"<strong class=sgf__code>{esc(v.code)}</strong></li>"
                f"<li><b>Come back here.</b> This page shows when {name} has signed the PC in.</li>"
            )
        if v.takes_code:
            box = (
                "<input type=text name=code id=sgcode autocomplete=off autocapitalize=off "
                f"autocorrect=off spellcheck=false maxlength={signins.MAX_CODE} required>"
                "<button class='btn btn--s' type=submit>Send code</button>"
            )
            steps += (
                f"<li><label for=sgcode>{name} then shows a code. Paste it here.</label>"
                + ctx.form("signin-code", box, "sgf__paste", always=True, flow=v.token)
                + f"{note}</li>"  # what the vendor said about the last code, under the box
            )
            note = ""
        body = (
            f"<ol class=sgf__steps>{steps}</ol>"
            "<p class=meta>The link works once and is shown only on this page.</p>"
        )
    ends = f" data-ends='{int(v.ends_at * 1000)}'" if v.mine and v.ends_at else ""
    return (
        f"<div class='sgf sgf--live'{ends}><div class=sgf__h><p class=sgf__title>{name} sign-in</p>"
        f"{_sc('run', 'In progress')}{stops}{cancel}</div>{body}{note}</div>"
    )


def _sgrow(r: signins.SignIn, ctx: Ctx, busy: bool) -> str:
    """One sign-in as a card: its name and state chip, how long it has, then Renew or why not."""
    state = _mstate(r.state)
    cls = f"sgrow sgrow--{state}" + (" sgrow--urgent" if r.urgent else "")
    line = " · ".join(d for d in (esc(r.detail), _moment(r.moment, ctx)) if d)
    why, act = esc(r.why_not), ""
    if r.help_url:  # the board's order: the link, then why Renew is not here
        act = (
            f'<a class=lnk href="{esc(r.help_url)}" target=_blank rel="noopener noreferrer">'
            f"{esc(r.help_label)} ↗</a>"
        )
    if r.renewable and busy:
        why = "A sign-in is running. Renew is back when it ends."
    elif r.renewable:
        act = (
            ctx.form(
                "signin",
                "<button class='btn btn--s' type=submit>Renew</button>",
                always=True,
                provider=r.key,
            )
            + act
        )
    return (
        f"<li class='{cls}'><div class=sgrow__top><b>{esc(r.label)}</b>{_mchip(state)}</div>"
        f"<p class=sgrow__big>{esc(r.headline)}</p>"
        + (f"<p class=sgrow__d>{line}</p>" if line else "")
        + f"<div class=sgrow__act>{act}"
        + (f"<p class=sgrow__why>{why}</p>" if why else "")
        + "</div></li>"
    )


def _signin_box(view: signins.FlowView, ctx: Ctx) -> str:
    active = " data-active" if view.active else ""
    return f"<section id=signin-flow class=sgflow{active}>{signin_flow(view, ctx)}</section>"


def render_signins(panel: signins.Panel, ctx: Ctx) -> str:
    """Each sign-in's state and expiry, and Renew where the desk can do it.

    This page is what any owner page gets, so it never holds a sign-in link or code: those
    reach only the page that started the flow, through ``signin_flow``.
    """
    rows = "".join(_sgrow(r, ctx, panel.flow.active) for r in panel.rows)
    counts = {
        s: sum(_mstate(r.state) == s for r in panel.rows) for s in ("down", "warn", "unknown", "ok")
    }
    summary = "".join(f"<li>{_mchip(s, f'{n} {_MSTATE[s]}')}</li>" for s, n in counts.items() if n)
    stamp = (
        f"<span class=stamp>Checked {ctx.time(_iso(panel.checked_at))}</span>"
        if panel.checked_at
        else ""
    )
    return _page(
        "Sign-ins · Hive desk",
        "<div class=ph><h1>Sign-ins</h1>"
        f"<ul class=mach__sum aria-label='Sign-in summary'>{summary}</ul>"
        f"<span class=sub>Renew a sign-in here before it runs out.</span>{stamp}</div>"
        "<div class='pgb sgb'>"
        + _signin_box(panel.flow, ctx)
        + _signin_poll(ctx, "", visible=False)
        + f"<ul class=sg>{rows}</ul></div>",
        "signins",
        f" data-refresh={SIGNINS_REFRESH_MS} data-signins",
        ctx,
    )


def render_signin_flow(view: signins.FlowView, ctx: Ctx) -> str:
    """The answer to a sign-in action when the page script is not running: the same panel on
    a page of its own, with a button to ask again."""
    poll = _signin_poll(ctx, view.token, visible=True) if view.active else ""
    return _page(
        "Sign-ins · Hive desk",
        "<div class=ph><h1>Sign-ins</h1></div><div class='pgb sgb'>"
        + _signin_box(view, ctx)
        + poll
        + "<p><a href='/signins'>Back to Sign-ins</a></p></div>",
        "signins",
    )


def render_confirm(title: str, detail: str, action: str, ctx: Ctx, hidden: dict[str, str]) -> str:
    """Step-up page: nothing runs until this form is posted back with ``step``."""
    inner = (
        f"<p>{esc(detail)}</p><div class=step__act>"
        "<button class=danger name=confirm value=1>Confirm</button>"
        f"<a class=lnk href='{esc(ctx.nxt)}'>Cancel</a></div>"
    )
    form = ctx.form(action, inner, **hidden)
    return _page(
        f"{title} · Hive desk",
        f"<div class=ph><h1>{esc(title)}</h1></div><section class='step step--ask'>{form}</section>",
    )


def render_result(title: str, message: str, nxt: str, ok: bool) -> str:
    cls = "banner ok" if ok else "banner"
    return _page(
        f"{title} · Hive desk",
        f"<div class=ph><h1>{esc(title)}</h1></div><section class=step>"
        f"<div class='{cls}' role=status>{esc(message)}</div>"
        f"<a class=lnk href='{esc(nxt)}'>Back</a></section>",
    )


def render_outcome(out: Outcome, nxt: str) -> str:
    return render_result(out.action.replace("-", " ").capitalize(), out.summary, nxt, True)


# ---- Terminal page ---------------------------------------------------------------------------
# Firstmate's own herdr pane, behind the step-up (see ``terminal.py``). The page carries no
# terminal data: it shows a "confirm it is you" screen, and only a finished step-up's pass
# opens the websocket that streams the screen.

VENDOR_DIR = Path(__file__).resolve().parent / "static" / "vendor"
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
