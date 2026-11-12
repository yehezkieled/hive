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
--line:rgba(41,37,30,.13);--line2:rgba(41,37,30,.28);
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
--line:rgba(247,242,232,.12);--line2:rgba(247,242,232,.27);
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
textarea,input[type=text],select,.inp{width:100%;min-height:44px;padding:9px 14px;border:1px solid var(--line2);border-radius:var(--rb);
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
/* bridge for page sections not yet on Comb; removed once every page is */
:root{--paper:var(--card);--paper-2:var(--bg);--paper-soft:var(--surface);--paper-shadow:var(--shadow);--ink-2:var(--ink);--ink-3:var(--muted);--ink-4:var(--muted);--rule:var(--line2);--rule-soft:var(--line2);--rule-faint:var(--line);--accent:var(--down);--accent-soft:var(--down-t);--amber:var(--warn);--amber-soft:var(--warn-t);--honey-soft:var(--you-t);--ochre:var(--action);--sage:var(--ok);--sage-soft:var(--ok-t);--bar:var(--ink);--bar-ink:var(--card);--font-mono:var(--fm);--font-sans:var(--ft);--font-display:var(--fd);--mute:var(--muted);--acc:var(--action)}
.ok{border-color:var(--ok)}.bubble{white-space:pre-wrap;overflow-wrap:anywhere}
.thread{display:flex;flex-direction:column;gap:10px;margin:12px 0}
.msg{max-width:85%;border-radius:14px;padding:8px 12px;border:1px solid var(--line);
background:var(--card)}
.msg.me{align-self:flex-end;border-color:var(--acc)}.msg.fm{align-self:flex-start;border-color:var(--ok)}
.msg .meta{display:block;color:var(--mute);font-size:.78rem;margin-top:4px}
.msg.is-err{border-color:var(--accent)}.msg.is-err .meta{display:flex;flex-wrap:wrap;align-items:center;gap:4px 10px}
.msg .meta button{min-height:44px;padding:0 16px}
.dock{position:sticky;bottom:0;background:var(--bg);padding:8px 0 calc(8px + env(safe-area-inset-bottom));
border-top:1px solid var(--line)}
.dock form{margin:0}.dock textarea{min-height:3rem}
/* chat: one full-height screen; only the message list scrolls, the box stays put */
body.chatpage{position:fixed;top:0;left:0;width:100%;height:100vh;height:var(--app-h,100dvh);overflow:hidden;
display:flex;flex-direction:column}
.chatpage main{flex:1;min-height:0;width:100%;box-sizing:border-box;display:flex;flex-direction:column;
position:relative;padding:0 12px}
.chatpage h1{flex:none;font-size:1.15rem;margin:8px 0 0}.chatpage .banner{flex:none}
.chatpage .thread{flex:1;min-height:0;margin:0;padding:12px 0;overflow-y:auto;overflow-x:hidden;
-webkit-overflow-scrolling:touch;overscroll-behavior:contain}
.chatpage .dock{flex:none;position:static;padding:8px 0 calc(8px + env(safe-area-inset-bottom))}
.chatpage .dock form{display:flex;gap:8px;align-items:flex-end}
.chatpage .dock label{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
.chatpage .dock textarea{flex:1;min-height:44px;max-height:30vh;resize:none;margin:0;font-size:16px}
.chatpage .dock button{flex:none;min-height:44px;min-width:64px}
#jump{position:absolute;left:50%;transform:translateX(-50%);bottom:calc(var(--dock-h,72px) + 8px);
min-height:44px;border-radius:999px;padding:0 16px;box-shadow:0 4px 14px var(--paper-shadow)}
#jump[hidden]{display:none}
@media (max-width:480px){.msg{max-width:94%}.dbar__go{padding:0 12px}}
/* the Stack home (docs/design/T002-stack-home.html) */
.screen{display:flex;flex-direction:column;gap:12px;font-size:13px;line-height:1.4}
.screen :where(p,h1,h2,h3,li,span,code,b){overflow-wrap:anywhere}
.screen form{margin:0}
.stamp{font:10px var(--font-mono);color:var(--ink-3);letter-spacing:.5px;margin:0 2px}
.land{display:grid;grid-template-columns:minmax(0,1fr);gap:12px;align-items:start}
.land__col{display:flex;flex-direction:column;gap:12px;min-width:0}
@media (min-width:900px){.land{grid-template-columns:minmax(0,1.15fr) minmax(0,1fr)}}
/* wide desk: use the width; the phone layout above is untouched */
@media (min-width:1100px){
.wide main{max-width:1480px}
.wide .screen{min-height:calc(100vh - 96px);gap:16px}
.wide .land{grid-template-columns:minmax(440px,5fr) minmax(0,7fr);gap:20px}
.wide .land__col{gap:16px}
.wide .pcs{grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}
.wide .dbar{padding:10px 10px 10px 16px;box-shadow:0 8px 24px var(--paper-shadow)}
.wide input.dbar__in{font-size:15px}
}
/* the desk is one screen at every size: the viewport is the page, only the content scrolls, and the
   delegate bar is its last row, flush with the viewport bottom (the safe-area inset lives inside it) */
body.wide{position:fixed;top:0;left:0;width:100%;height:100vh;height:var(--app-h,100dvh);overflow:hidden;
display:flex;flex-direction:column;padding-bottom:0}
.wide main{flex:1;min-height:0;width:100%;display:flex;flex-direction:column;padding:8px 0 0;overflow-y:auto}
.wide .screen{flex:1;min-height:0;gap:10px}
.wide .land{flex:1;min-height:0;overflow-y:auto;overflow-x:hidden;-webkit-overflow-scrolling:touch;overscroll-behavior:contain}
.wide .dbar-wrap{position:static;margin:0;flex:none;padding-bottom:calc(8px + env(safe-area-inset-bottom))}
/* on PC, laptop and tablet each panel scrolls on its own */
@media (min-width:700px) and (min-height:560px){
.wide .land{overflow:visible;grid-template-columns:minmax(0,1fr) minmax(0,1fr);grid-template-rows:minmax(0,1fr);gap:14px;align-items:stretch}
.wide .land__col{min-height:0;gap:10px}
.wide .nyl{display:flex;flex-direction:column;min-height:0;height:100%}
.wide .nyl__head{flex:none}
.wide .nyl__body{flex:1;min-height:0;overflow-y:auto;overflow-x:hidden;-webkit-overflow-scrolling:touch;overscroll-behavior:contain}
.wide .pcsec{display:flex;flex-direction:column;gap:6px;min-height:0;flex:0 1 auto;max-height:55%}
.wide .pcs{overflow-y:auto;overflow-x:hidden;-webkit-overflow-scrolling:touch;overscroll-behavior:contain;
align-content:start;grid-auto-rows:max-content;min-height:0;padding-bottom:2px}
.wide .rvs{flex:1 1 0;min-height:0}
.wide .rv__box{flex:1;min-height:0}
.wide .rv__list{flex:1;max-height:none;min-height:0}
.wide .stamp{flex:none}
}
@media (min-width:1100px) and (min-height:560px){.wide .land{grid-template-columns:minmax(440px,5fr) minmax(0,7fr)}}
@media (min-width:700px) and (min-height:560px) and (max-width:1099px) and (orientation:portrait){
.wide .land{grid-template-columns:minmax(0,1fr);grid-template-rows:minmax(0,1.1fr) minmax(0,1fr)}
.wide .land__col:last-child{flex-direction:row}
.wide .land__col:last-child>*{flex:1 1 0;min-width:0}
.wide .pcs{grid-template-columns:minmax(0,1fr)}
.wide .pcsec{max-height:none}
}
.nyl{background:var(--paper);border:1.5px solid var(--rule);border-radius:16px;
box-shadow:0 8px 24px var(--paper-shadow);overflow:hidden}
.nyl__head{display:flex;align-items:center;gap:9px;padding:13px 16px;border-bottom:1.5px solid var(--rule)}
.nyl__head>*{min-width:0}
.nyl__glyph{color:var(--accent);font-size:14px;line-height:1}
.nyl__title{font-family:var(--font-display);font-weight:800;font-size:14px;margin:0}
.nyl__count{margin-left:auto;font:700 10px var(--font-mono);letter-spacing:1px;background:var(--accent);
color:#fff;padding:2px 9px;border-radius:999px}
.nyl__body{padding:12px;display:flex;flex-direction:column;gap:10px}
.state-dot{width:9px;height:9px;border-radius:50%;flex-shrink:0}
.state-dot--active{background:var(--sage)}.state-dot--idle{background:var(--ink-4)}
.state-dot--error{background:var(--accent)}.state-dot--gated{background:var(--honey)}
.nyi__entity{font:600 12px var(--font-mono);color:var(--ink)}
.nyi__summary{width:100%;color:var(--ink-2);font-size:12.5px;margin:0}
.nyi__summary b{color:var(--ink);font-weight:700}
.dq__q{display:block;font-weight:600;color:var(--ink);line-height:1.4}
.nyi__summary>b+.dq__q{margin-top:2px}
.dq__opts{list-style:none;margin:6px 0 0;padding:0;display:flex;flex-direction:column;gap:4px}
.dq__opt{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 8px;padding:5px 8px;border:1px solid var(--rule-faint);border-radius:6px}
.dq__opt b{font:700 .8rem var(--font-mono);color:var(--ink)}
.dq__opt.is-rec{border-color:var(--honey);background:rgba(200,150,40,.12);color:var(--ink)}
.dq__rec{margin-left:auto;font:700 10px var(--font-mono);text-transform:uppercase;letter-spacing:.04em;color:var(--ink-2)}
.dq__more{margin-top:6px}
.dq__more summary{display:inline-flex;align-items:center;min-height:44px;min-width:44px;cursor:pointer;font-weight:700;color:var(--ink-2)}
.dq__more p{margin:0 0 4px;white-space:pre-wrap;overflow-wrap:anywhere}
.nyi__actions{display:flex;align-items:center;gap:7px;flex-wrap:wrap}
.nyi__actions>*{min-width:0}
.nyi__btns{display:flex;gap:7px;flex-wrap:wrap}
input.nyi__reply{flex:1 1 180px;min-width:0;width:auto;min-height:44px;font:12px var(--font-mono);padding:6px 9px;
border:1px solid var(--rule-soft);border-radius:7px;background:var(--paper);color:var(--ink)}
.nyg{display:flex;flex-direction:column;gap:6px}
.nyg+.nyg{border-top:1px solid var(--rule-faint);padding-top:10px}
.nyg__head{display:flex;align-items:center;gap:8px;min-height:44px;color:inherit;text-decoration:none;flex-wrap:wrap}
.nyg__head>*{min-width:0}
.nyg__name{font:700 13px var(--font-display)}
.nyg__mate{font:9px var(--font-mono);color:var(--ink-4)}
.nyg__wait{font:700 9px var(--font-mono);letter-spacing:.5px;color:var(--ochre);background:var(--honey-soft);
border-radius:999px;padding:2px 8px;white-space:nowrap}
.nyg__count{margin-left:auto;font:700 10px var(--font-mono);color:var(--ink-3)}
.nyg__items{display:flex;flex-direction:column;gap:2px}
.nyx{position:relative;border:1px solid transparent;border-left:4px solid transparent;border-radius:12px}
.nyx__bar{display:flex;align-items:center;gap:2px}
.nyx__head{flex:1;min-width:0;display:flex;flex-wrap:wrap;align-items:center;gap:2px 8px;min-height:44px;padding:4px 6px;
color:inherit;text-decoration:none;border-radius:7px;cursor:pointer}
.nyx__head:hover{background:var(--paper-soft)}
.nyx__head:focus-visible,.nyx__open:focus-visible{outline:2px solid var(--ochre);outline-offset:-2px}
.nyx__id{font:10px var(--font-mono);color:var(--ink-4);flex-shrink:0;max-width:34%;overflow:hidden;
text-overflow:ellipsis;white-space:nowrap}
.nyx__title{flex:1 1 150px;min-width:0;font-size:12.5px;color:var(--ink-2);overflow:hidden;text-overflow:ellipsis;
white-space:nowrap}
.nyx__tag{font:700 8.5px var(--font-mono);letter-spacing:1px;text-transform:uppercase;color:var(--ink-3);
border:1px solid var(--rule-faint);border-radius:999px;padding:1px 7px;background:var(--paper-soft);
white-space:nowrap}
.nyx__tag--hold{color:var(--ochre);border-color:var(--honey-soft);background:var(--honey-soft)}
.nyx__open{font:9px var(--font-mono);color:var(--ink-4);white-space:nowrap;min-height:44px;min-width:44px;
display:inline-flex;align-items:center;justify-content:center;padding:0 6px;border-radius:7px;text-decoration:none}
.nyx__open:hover{background:var(--paper-soft);color:var(--ink-2)}
.nyx .state-dot,.nyx__more{display:none}
.nyx.is-primary{background:var(--paper);border-color:var(--rule-soft);padding:2px 8px 12px;margin:4px 0}
.nyx.is-primary.nyi--decision{border-left-color:var(--honey)}
.nyx.is-primary.nyi--gate{border-left-color:var(--ochre)}
.nyx.is-primary.nyi--mode{border-left-color:var(--amber)}
.nyx.is-primary.nyi--queued{border-left-color:var(--sage)}
.nyx.is-primary.nyi--blocked{border-left-color:var(--ink-4)}
.nyx.is-primary .state-dot{display:block}
.nyx.is-primary .nyx__title{display:none}
.nyx.is-primary .nyx__id{font:600 12px var(--font-mono);color:var(--ink);max-width:none}
.nyx.is-primary .nyx__head:hover{background:none}
.nyx.is-primary .nyx__more{display:block;padding:0 4px}
.nyx__more>.nyi__summary{margin:2px 0 6px}
.nyx__desc{margin:0 0 8px;font-size:12.5px;line-height:1.45;color:var(--ink-3);border-left:2px solid var(--rule-faint);
padding-left:9px;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:4;line-clamp:4;overflow:hidden}
.nyx__desc[data-ready]{cursor:pointer}
.nyx__desc[data-ready]:hover{color:var(--ink-2)}
.nyx__desc:focus-visible{outline:2px solid var(--ochre);outline-offset:2px}
.nyx__desc[hidden]{display:none}
.nyx__desc.is-pending{color:var(--ink-4);animation:nyx-pulse 1.4s ease-in-out infinite}
.nyx.is-flip{transition:transform .3s cubic-bezier(.2,.8,.2,1);will-change:transform}
.nyx.is-lifted{z-index:2}
.nyx.is-opening .nyx__more{animation:nyx-in .28s ease-out both}
@keyframes nyx-in{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:none}}
@keyframes nyx-pulse{50%{opacity:.45}}
@media (prefers-reduced-motion:reduce){.nyx,.nyx__more,.nyx__desc{animation:none!important;transition:none!important}}
.nyq__more{display:inline-flex;align-items:center;font:10px var(--font-mono);color:var(--ink-3);text-decoration:none;
padding:0 4px;min-height:44px}
.rvs{display:flex;flex-direction:column;gap:6px}
.rv__box{display:flex;flex-direction:column;background:var(--paper);border:1.5px solid var(--rule);border-radius:12px;overflow:hidden}
.rv__list{display:flex;flex-direction:column;max-height:min(44vh,300px);overflow-y:auto;overflow-x:hidden;
-webkit-overflow-scrolling:touch;overscroll-behavior:contain}
.rvrow{display:flex;align-items:stretch;border-top:1px solid var(--rule-faint);flex:none}
.rvrow:first-child{border-top:0}
.rvrow:hover{background:var(--paper-soft)}
.rvrow>form{display:flex;margin:0;flex:none}
.rv{display:flex;align-items:center;gap:8px;flex:1;min-width:0;min-height:48px;padding:8px 6px 8px 12px;color:inherit;text-decoration:none}
.rv>*{min-width:0}
.rv__title{flex:1;font-size:13px;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rv__proj{font:10px var(--font-mono);color:var(--ink-3);max-width:34%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rv__reply{font:700 8.5px var(--font-mono);letter-spacing:1px;text-transform:uppercase;color:var(--ochre);
background:var(--honey-soft);border-radius:999px;padding:2px 8px}
.rv__go{font:10px var(--font-mono);color:var(--ink-4)}
.rv__done{font:700 8.5px var(--font-mono);letter-spacing:1px;text-transform:uppercase;color:var(--ink-3);
border:1px solid var(--rule);border-radius:999px;padding:1px 7px;white-space:nowrap}
.rv__x{min-width:56px;min-height:44px;align-self:center;padding:0 10px;border:0;border-radius:8px;background:transparent;
color:var(--ink-3);font:700 10px var(--font-mono);cursor:pointer;white-space:nowrap}
.rv__x:hover{background:var(--paper-soft);color:var(--ink)}
.rv__x.is-armed{background:var(--accent);color:#fff}
.rv__x--bulk{border:1.5px solid var(--rule);margin-right:4px}
form.is-sending .rv__x{opacity:.5}
.rvs__nudge{display:flex;align-items:center;flex-wrap:wrap;gap:6px 8px;padding:6px 6px 6px 12px;background:var(--honey-soft);
border-radius:12px;font-size:12.5px;color:var(--ink)}
.rvs__nudge>span{flex:1 1 160px;min-width:0}
.rvs__nudge form{display:flex;margin:0}
.rv__review{min-height:44px;padding:0 12px;border:1.5px solid var(--rule);border-radius:8px;background:transparent;
color:var(--ink);font:700 10px var(--font-mono);cursor:pointer}
.rv__morebtn{display:flex;align-items:center;width:100%;min-height:44px;padding:0 12px;border:0;border-top:1px solid var(--rule-faint);
border-radius:0;background:transparent;color:var(--ink-3);font:10px var(--font-mono);cursor:pointer;text-align:left}
.rv__morebtn:hover{background:var(--paper-soft)}
.sheet{width:min(560px,calc(100vw - 24px));max-height:min(80vh,calc(100dvh - 24px));margin:auto;padding:0;
background:var(--paper);color:var(--ink);border:1.5px solid var(--rule);border-radius:16px;
box-shadow:0 16px 48px var(--paper-shadow);overflow:hidden}
.sheet[open]{display:flex;flex-direction:column;animation:sheet-in .18s ease-out}
.sheet.is-restored[open]{animation:none}
.sheet{cursor:pointer}.sheet>*{cursor:auto}
body:has(dialog[open]){overflow:hidden}
.sheet::backdrop{background:rgba(20,16,12,.5)}
.sheet__head{display:flex;align-items:center;gap:8px;padding:4px 6px 4px 16px;border-bottom:1.5px solid var(--rule);flex:none}
.sheet__head>*{min-width:0}
.sheet__head h2{flex:1;margin:0;font:800 .95rem var(--font-display);overflow-wrap:anywhere}
.sheet__x{min-width:44px;min-height:44px;padding:0;border:0;background:transparent;color:var(--ink);font-size:26px;line-height:1}
.sheet__list{display:flex;flex-direction:column;overflow-y:auto;overflow-x:hidden;min-height:0;
-webkit-overflow-scrolling:touch;overscroll-behavior:contain;padding-bottom:env(safe-area-inset-bottom)}
.sheet__meta{margin:0;padding:10px 16px;font-size:12.5px;color:var(--ink-2);border-bottom:1px solid var(--rule-faint);flex:none}
.sheet__text{margin:0;padding:14px 16px;font-size:14px;line-height:1.55;color:var(--ink-2);white-space:pre-wrap;overflow-wrap:anywhere}
.sheet__about{margin:0;padding:12px 16px 4px;font-size:14px;line-height:1.5;color:var(--ink);overflow-wrap:anywhere;flex:none}
.sheet__empty{margin:0;padding:14px 16px;font:10px var(--font-mono);color:var(--ink-3)}
@keyframes sheet-in{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
@media (prefers-reduced-motion:reduce){.sheet[open]{animation:none}}
.nyi__reply::placeholder{color:var(--ink-4)}
.btn--go{background:var(--ink);color:var(--paper)}
.btn--allow{border-color:var(--sage);color:var(--sage)}
.btn--deny{border-color:var(--rule-soft);color:var(--ink-3)}
.calm{text-align:center;padding:16px 16px 18px}
.calm__check{font-size:20px;color:var(--sage);line-height:1}
.calm__line{font-family:var(--font-display);font-weight:800;font-size:15px;margin:8px 0 2px}
.calm__sub{font:10px var(--font-mono);color:var(--ink-3);letter-spacing:.5px;margin:0}
.nyl--calm .nyl__head{border-bottom-color:var(--rule-faint)}
.nyl--calm .nyl__glyph{color:var(--sage)}
.nyl--calm .nyl__count{background:var(--sage-soft);color:var(--ochre)}
.cfg h2{margin:1.2rem 0 .4rem}
.cfg .tblw{overflow-x:auto;border:1.5px solid var(--rule-soft);border-radius:10px;background:var(--paper)}
.cfg table{border-collapse:collapse;width:100%;font-size:.88rem}
.cfg th,.cfg td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--rule-faint);vertical-align:top;overflow-wrap:anywhere}
.cfg th{font:700 9.5px var(--font-mono);letter-spacing:1.2px;text-transform:uppercase;color:var(--ink-3)}
.cfg th{white-space:nowrap}.cfg td:first-child{white-space:nowrap}
.cfg tr:last-child td{border-bottom:0}
.cfg .unk{color:var(--accent);font-weight:700}
.cfg ul{list-style:none;margin:0;padding:0;display:grid;gap:8px}
.cfg li{background:var(--paper);border:1.5px solid var(--rule-soft);border-radius:10px;padding:9px 11px;min-width:0}
.cfg .when{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;font-size:.88rem}
.cfg .prof{font:11px var(--font-mono);color:var(--ink-2);margin-top:4px;overflow-wrap:anywhere}
.cfg .kv{font:12px var(--font-mono);display:flex;flex-wrap:wrap;gap:4px 14px;margin:0}
.sec-label{font:700 9.5px var(--font-mono);letter-spacing:1.4px;text-transform:uppercase;color:var(--ink-3);margin:2px 2px -4px}
.pcs{display:grid;grid-template-columns:minmax(0,1fr);gap:10px}
.pc{display:flex;flex-direction:column;gap:7px;text-align:left;width:100%;min-height:44px;font:inherit;color:inherit;
text-decoration:none;background:var(--paper);border:1.5px solid var(--rule-soft);border-left-width:4px;border-radius:12px;
padding:11px 12px;cursor:pointer}
.pc:hover{background:var(--paper-soft)}
.pc--running{border-left-color:var(--sage)}.pc--idle{border-left-color:var(--ink-4)}.pc--blocked{border-left-color:var(--accent)}
.pc.is-selected{border-color:var(--rule);border-left-width:4px;background:var(--honey-soft);
box-shadow:0 0 0 2px var(--paper),0 0 0 3.5px var(--ink)}
.pc__top{display:flex;align-items:center;gap:8px}
.pc__top>*{min-width:0}
.aka{font:10px var(--font-mono);color:var(--ink-4);font-weight:400;margin-left:6px;white-space:nowrap}
.pc__name{font-family:var(--font-display);font-weight:800;font-size:14px}
.pc__mae{font:10px var(--font-mono);color:var(--ink-3);flex:none;white-space:nowrap}
.pc__status{margin-left:auto;flex:none;white-space:nowrap;font:700 8.5px var(--font-mono);letter-spacing:1px;text-transform:uppercase;
border-radius:999px;padding:2px 8px;border:1px solid var(--rule-faint);background:var(--paper-soft);color:var(--ink-3)}
.pc--running .pc__status{background:var(--sage-soft);color:var(--sage);border-color:var(--sage-soft)}
.pc--blocked .pc__status{background:var(--accent-soft);color:var(--accent);border-color:var(--accent-soft)}
.pc__now{font-size:12px;color:var(--ink-2);margin:0}
.pc__now b{color:var(--ink)}
.pc__prog{display:flex;align-items:center;gap:8px;font:10px var(--font-mono);color:var(--ink-3)}
.pc__track{flex:1;height:6px;border-radius:3px;background:var(--rule-faint);overflow:hidden}
.pc__track i{display:block;height:100%;background:var(--sage)}
.pc--blocked .pc__track i{background:var(--accent)}.pc--idle .pc__track i{background:var(--ink-4)}
.pc__open{font:9px var(--font-mono);color:var(--ink-4);display:none}
.pc.is-selected .pc__open{display:inline;color:var(--ink-2);font-weight:600}
.dbar{display:flex;align-items:center;gap:8px;background:var(--bar);color:var(--bar-ink);border-radius:16px;padding:8px 8px 8px 12px}
.dbar>*{min-width:0}
.dbar__target{font:700 10px var(--font-mono);letter-spacing:.6px;white-space:nowrap;background:var(--honey);
color:#1f1812;border-radius:999px;padding:4px 10px;overflow:hidden;text-overflow:ellipsis;max-width:45%}
input.dbar__in{flex:1;min-width:0;width:auto;min-height:44px;background:transparent;border:0;color:var(--bar-ink);
font:13px var(--font-sans);outline:none;padding:0}
.dbar__in::placeholder{color:#a79a89}
.dbar__go{font:700 11px var(--font-mono);border:0;border-radius:10px;padding:0 16px;min-height:44px;
background:var(--bar-ink);color:var(--bar);cursor:pointer}
.dbar-info{display:flex;flex-wrap:wrap;align-items:baseline;gap:2px 12px;margin:0 4px 6px}
.dbar-note{font:9px var(--font-mono);color:var(--ink-3);margin:0}
.dbar-info .stamp{margin:0}
.dbar-flash{font:700 11px var(--font-mono);color:var(--ochre);margin:0 4px 6px}
.dbar-flash--err{color:var(--accent)}
.dbar-flash[hidden]{display:none}
.dbar-wrap:has(.dbar-flash:not([hidden])) .dbar-info{display:none}
.dbar.is-sending{opacity:.7}
form.is-sent button{opacity:.75}form.is-sent button.is-sent{color:var(--ochre)}
.act-err{font:700 11px var(--font-mono);color:var(--accent);margin:6px 0 0}
.act-warn{font:700 11px/1.4 var(--font-mono);color:var(--accent);margin:8px 0 0;white-space:normal}
/* the Machine page: the chain that has to hold and the upcoming risks; read-only */
.mach{display:flex;flex-direction:column;gap:10px}
.mach__head{display:flex;flex-wrap:wrap;align-items:center;gap:4px 14px;margin-top:.5rem}
.mach h1{margin:0}
.mach__lead{display:flex;flex-wrap:wrap;align-items:baseline;gap:2px 12px;margin:0}
.mach__lead p{margin:0}
.mach__term a{font:700 12px var(--font-mono);letter-spacing:.02em}
.mach__sum{display:flex;flex-wrap:wrap;gap:6px;list-style:none;margin:0;padding:0}
.mach__sum .mstate{min-width:0}
.mach__cols{display:grid;grid-template-columns:minmax(0,1fr);gap:14px;align-items:start}
.mach__col{display:flex;flex-direction:column;gap:8px;min-width:0}
.mach__col h2{margin:2px 2px -2px}
@media (min-width:1000px){.mach__cols{grid-template-columns:minmax(0,3fr) minmax(0,2fr)}}
.mlist{list-style:none;margin:0;padding:0;display:grid;gap:6px}
.mrow{display:grid;grid-template-columns:auto minmax(0,1fr) auto;align-items:center;gap:2px 10px;min-width:0;
background:var(--paper);border:1.5px solid var(--rule-soft);border-left:4px solid var(--ink-4);border-radius:10px;padding:8px 10px}
.mrow--ok{border-left-color:var(--sage)}
.mrow--warn{border-left-color:var(--amber);background:var(--amber-soft)}
.mrow--down,.mrow--urgent{border-left-color:var(--accent);background:var(--accent-soft)}
.mstate{display:inline-flex;align-items:center;justify-content:center;gap:5px;min-width:82px;white-space:nowrap;
font:700 10px var(--font-mono);letter-spacing:.6px;text-transform:uppercase;color:var(--ink);
background:var(--paper);border:1.5px solid var(--ink-4);border-radius:999px;padding:3px 8px}
.mstate::before{content:'?';color:var(--ink-3)}
.mstate--ok{border-color:var(--sage)}.mstate--ok::before{content:'✓';color:var(--sage)}
.mstate--warn{border-color:var(--amber)}.mstate--warn::before{content:'!';color:var(--amber)}
.mstate--down{border-color:var(--accent)}.mstate--down::before{content:'✕';color:var(--accent)}
.mstate--run{border-color:var(--honey)}.mstate--run::before{content:'…';color:var(--ochre)}
.mrow--urgent .mstate{border-color:var(--accent)}.mrow--urgent .mstate::before{color:var(--accent)}
.mrow__body{min-width:0}
.mrow__top{display:flex;flex-wrap:wrap;align-items:baseline;gap:0 8px}
.mrow__top b{font:800 14px var(--font-display)}
.mrow__head{font-size:13px}
.mrow__detail{margin:1px 0 0;font-size:12px;color:var(--ink-2);overflow-wrap:anywhere}
.mrow__detail time{white-space:nowrap}
.mrow__side{font:10px var(--font-mono);color:var(--ink-3);text-align:right;white-space:nowrap}
.mrow__due{font-weight:700;font-size:11px;color:var(--ink)}
@media (max-width:480px){.mrow{grid-template-columns:auto minmax(0,1fr)}.mrow__side{grid-column:2;text-align:left}}
/* the Run page: fixed fix buttons and the last runs */
.runq{display:flex;flex-direction:column;gap:10px}
.runq__cols{display:grid;grid-template-columns:minmax(0,1fr);gap:14px;align-items:start}
.runq__col{display:flex;flex-direction:column;gap:8px;min-width:0}
.runq__col h2{margin:8px 2px -2px}.runq__col h2:first-child{margin-top:2px}
@media (min-width:1000px){.runq__cols{grid-template-columns:minmax(0,3fr) minmax(0,2fr)}
/* the runs column is narrow: each run's time goes under its words, its chip at the top */
.rqrun{grid-template-columns:auto minmax(0,1fr);align-items:start}.rqrun .mrow__side{grid-column:2;text-align:left}}
.runq .banner{margin:0}.runq .banner p{margin:0}.runq .banner p+p{margin-top:4px}
.rqlist{list-style:none;margin:0;padding:0;display:grid;gap:8px}
.rq{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:start;gap:4px 14px;min-width:0;
background:var(--paper);border:1.5px solid var(--rule-soft);border-left:4px solid var(--ink-4);border-radius:10px;padding:10px 12px}
.rq--down{border-left-color:var(--accent)}.rq--off{background:var(--paper-soft);border-left-color:var(--rule-soft)}
.rq__top{display:flex;flex-wrap:wrap;align-items:baseline;gap:0 8px}
.rq__top b{font:800 14px var(--font-display)}
.rq__took{font:10px var(--font-mono);color:var(--ink-3);white-space:nowrap}
.rq__what{margin:2px 0 0;font-size:13px;color:var(--ink-2)}
.rq__cmd{display:inline-block;max-width:100%;margin-top:6px;font:11px/1.4 var(--font-mono);color:var(--ink);
background:var(--paper-soft);border:1px solid var(--rule-faint);border-radius:6px;padding:3px 7px;overflow-wrap:anywhere}
.rq__off{margin:6px 0 0;font:700 11px var(--font-mono);color:var(--ink-3)}
.rq form{margin:0;display:flex;flex-wrap:wrap;gap:6px;justify-content:flex-end;align-items:center;max-width:300px}
.rq form label{margin:0;font-size:12px}
.rq select{width:auto;min-height:44px;padding:0 8px}
.rq button{white-space:nowrap}
.rq form .act-warn,.rq form .act-err,.rq form .act-ok{flex-basis:100%;text-align:left}
/* the confirm step's words: read first, then tap; calm, with a red edge on the ones that take things down */
.rq form .act-warn{order:-1;margin:2px 0 4px;font-family:inherit;font-size:13px;line-height:1.45;font-weight:600;
letter-spacing:0;color:var(--ink);background:var(--paper-soft);border:1px solid var(--rule-soft);
border-left:3px solid var(--ink-4);border-radius:8px;padding:8px 10px}
.rq--down form .act-warn{border-left-color:var(--accent)}
.act-ok{font:700 11px/1.4 var(--font-mono);color:var(--sage);margin:6px 0 0;white-space:normal}
@media (max-width:640px){.rq{grid-template-columns:minmax(0,1fr)}.rq form{justify-content:flex-start;max-width:none}}
.rqnote{margin:0;font-size:12px;color:var(--ink-3)}
.rqrun .mrow__detail{white-space:normal}.rqrun .mstate{min-width:100px}
/* an armed or answered button's words take the card's whole width, under its text */
.rq form[data-step],.rq form.is-shown{grid-column:1/-1;max-width:none;justify-content:flex-start}
.mach__lead a{white-space:nowrap}
/* the Sign-ins page: each sign-in's state, and the one renewal that may be running */
.sgrow__act{display:flex;flex-direction:column;align-items:flex-end;min-width:0}
.sgrow__act form{margin:0;display:flex;flex-direction:column;align-items:flex-end}
.sgrow__act button{white-space:nowrap}
.sgrow__act button.is-armed{background:var(--accent);border-color:var(--accent);color:#fff}
.sgrow__act:has(.act-warn,.act-err){grid-column:1/-1;align-items:flex-start;margin-top:4px}
.sgrow__act:has(.act-warn,.act-err) form{align-items:flex-start}
.sgrow__why{margin:3px 0 0;font-size:12px;color:var(--ink-3);overflow-wrap:anywhere}
.sgrow__why a{white-space:nowrap}
@media (max-width:480px){.sgrow__act{grid-column:2;align-items:flex-start;margin-top:4px}}
.sgflow:empty{display:none}
.sgf{background:var(--paper);border:1.5px solid var(--rule);border-radius:12px;padding:12px 14px;
display:flex;flex-direction:column;gap:8px;min-width:0}
.sgf--ok{border-color:var(--sage)}.sgf--failed{border-color:var(--accent)}.sgf--quiet{border-color:var(--rule-soft)}
.sgf p{margin:0}
.sgf__title{font:800 15px var(--font-display);display:flex;flex-wrap:wrap;align-items:baseline;gap:0 8px}
.sgf__title .mute{font:400 12px var(--font-sans)}
.sgf__steps{margin:0;padding-left:22px;display:flex;flex-direction:column;gap:12px;font-size:14px}
.sgf__steps label{color:var(--ink);font-size:14px;margin:0}
.sgf__link{display:flex;align-items:center;justify-content:center;min-height:56px;margin:6px 0 4px;padding:8px 16px;
border-radius:10px;background:var(--ink);color:var(--paper);font:800 16px var(--font-display);text-decoration:none;text-align:center}
.sgf__link:active{transform:translateY(1px)}
.sgf__host{font:11px var(--font-mono);color:var(--ink-3)}
.sgf__code{display:inline-block;margin-top:6px;padding:8px 14px;border:1.5px dashed var(--rule);border-radius:8px;
font:700 24px var(--font-mono);letter-spacing:3px;user-select:all;-webkit-user-select:all}
.sgf__paste{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0 0}
.sgf__paste input{flex:1 1 180px;min-width:0;width:auto;min-height:44px;font:14px var(--font-mono)}
.sgf__paste .act-err{flex-basis:100%;margin:0}
.sgf__note{font:700 12px/1.4 var(--font-mono);color:var(--accent)}
.sgf__steps .sgf__note{margin-top:6px}
.sgf__foot{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:6px 12px}
.sgf__foot form{margin:0}
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
o.outerHTML=n.outerHTML;
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
function panels(v){return ['.nyl__body','.pcs','.rv__list'].map(function(s,i){var e=c.querySelector(s);
if(!e)return 0;if(v)e.scrollTop=v[i];return e.scrollTop;});}
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
var near=window.innerHeight+window.scrollY>=document.documentElement.scrollHeight-120;
if(pre.textContent!==j.text){pre.textContent=j.text;if(near)bottom();}
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
if(hit)pick(g[i],hit,false);else delete picks[k];}sync(g[i]);}describeAll();barRoom();sgRestore();}
document.addEventListener('click',function(e){
var h=e.target.closest?e.target.closest('.nyx__head'):null;
if(!h||e.button||e.metaKey||e.ctrlKey||e.shiftKey||e.altKey)return;
var it=h.closest('.nyx'),g=it.closest('[data-group]'),k=g.getAttribute('data-group'),now=Date.now(),
pos=[].indexOf.call(g.querySelectorAll('.nyx'),it);
e.preventDefault();
if(lastPick&&lastPick.k===k&&now-lastPick.t<450&&g.querySelector('.nyx.is-primary')===lastPick.it&&
(it===lastPick.it||pos===lastPick.pos)){lastPick=null;location.href=g.getAttribute('data-open');return;}
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
if(!th&&document.body&&document.body.classList&&document.body.classList.contains('wide')&&window.visualViewport){var dv=window.visualViewport;
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
        "<meta name=theme-color content='#b4531a'>"
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


def _structured(text: str, ctx: Ctx) -> str:
    """A decision text as a card body: the question, a short option list with the recommended
    one marked, and the full wording behind a native "More" disclosure."""
    card = parse_card(text)
    if not card.question:
        return ""
    out = f"<span class=dq__q>{ctx.plain(card.question)}</span>"
    if card.options:
        out += (
            "<ul class=dq__opts>"
            + "".join(
                f"<li class='dq__opt{' is-rec' if o.recommended else ''}'><b>{esc(o.letter)}</b> "
                f"{ctx.plain(o.label)}"
                + ("<span class=dq__rec>recommended</span>" if o.recommended else "")
                + "</li>"
                for o in card.options
            )
            + "</ul>"
        )
    if card.more:
        out += f"<details class=dq__more><summary>More</summary><p>{ctx.plain(card.more)}</p></details>"
    return out


_NEED_LABEL = {"hold": "Question on hold", "decision": "Decision", "merge": "Merge approval"}


def _posture_tag(project: str, ctx: Ctx) -> str:
    """Firstmate's merge posture for the card's project, read-only; ``unknown`` says why."""
    if ctx.postures is None:
        return ""
    label = fmconfig.posture_label(ctx.postures, project)
    return f"<span class=sub>Merge posture: {esc(label)} · <a href='/config'>config</a></span>"


def _need(n: NeedsYou, show_project: bool, ctx: Ctx) -> str:
    url = ctx.link(n.url)
    link = f' <a href="{esc(url)}" rel="noopener noreferrer">Open PR</a>' if url else ""
    link += "".join(
        f' <a href="{esc(b)}" rel="noopener noreferrer">Open board</a>' for b in ctx.boards(n.text)
    )
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
            "<button>Record answer</button>",
            task=n.ref,
            rid=new_request_id(),
        )
    elif n.kind == "decision":
        task, _, key = n.ref.partition("/")
        act = ctx.form(
            "decision",
            "<label>Your answer (sent to the first mate)</label>"
            "<textarea name=text required maxlength=4000></textarea><button>Send answer</button>",
            task=task,
            key=key,
            rid=new_request_id(),
        )
    elif n.kind == "merge":
        act = ctx.form(
            "merge",
            "<button class=quiet>Give merge word…</button>",
            task=n.ref,
            rid=new_request_id(),
        ) + _posture_tag(n.project, ctx)
    detail = f"{_NEED_LABEL.get(n.kind, n.kind)} · {n.ref}"
    return (
        f"<div class=need><span class=what>{esc(lead)}</span>{link}"
        f"<div class=dq>{_structured(n.text, ctx)}</div><span class=sub>{esc(detail)}</span>{act}</div>"
    )


# Needs-you kinds on the T001 lane: (row class, state dot, badge).
_LANE = {
    "decision": ("nyi--decision", "idle", "decision"),
    "hold": ("nyi--gate", "gated", "waiting on you"),
    "merge": ("nyi--mode", "active", "merge"),
}


def _hold_parts(n: NeedsYou, ctx: Ctx) -> tuple[str, str, str, str, str]:
    """A captain hold's lane parts: row class, state dot, badge, summary and the answer form."""
    row, dot, badge = _LANE.get(n.kind, ("nyi--decision", "idle", n.kind))
    ref = n.ref.partition("/")[0]
    entity = f"{ctx.show(n.project)} · {ref}"  # the reply label; the lane's group header names the project
    if n.title and n.text and n.title != n.text:
        summary = f"<b>{ctx.plain(n.title)}</b>{_structured(n.text, ctx)}"
    else:
        summary = _structured(n.text, ctx) or ctx.plain(n.title or n.ref)
    links = [(url, "Open board ↗") for url in ctx.boards(n.text)]
    pr = ctx.link(n.url)
    if pr:
        links.append((pr, "Open PR ↗"))
    extra = "".join(
        f'<a class="btn btn--deny" href="{esc(url)}" rel="noopener noreferrer">{label}</a>'
        for url, label in links
    )
    reply = (
        "<input type=text class=nyi__reply name=text required "
        f"maxlength=MAX placeholder='Reply to {esc(entity)}…' aria-label='Reply to {esc(entity)}'>"
    )
    if n.kind == "decision":
        task, _, key = n.ref.partition("/")
        act = ctx.form(
            "decision",
            reply.replace("MAX", "4000", 1)
            + f"<span class=nyi__btns><button class='btn btn--go'>Send</button>{extra}</span>",
            "nyi__actions",
            task=task,
            key=key,
            rid=new_request_id(),
        )
    elif n.kind == "hold":
        resume = "<button class='btn btn--{}' name=release value=1>Answer &amp; resume</button>"
        close = "<button class='btn btn--{}' name=release value=0>Answer &amp; close</button>"
        buttons = (
            resume.format("go") + close.format("deny")
            if n.gated
            else close.format("go") + resume.format("deny")
        )
        act = ctx.form(
            "answer",
            reply.replace("MAX", "512", 1) + f"<span class=nyi__btns>{buttons}{extra}</span>",
            "nyi__actions",
            task=n.ref,
            rid=new_request_id(),
        )
    elif n.kind == "merge":
        act = ctx.form(
            "merge",
            f"<button class='btn btn--allow'>Give merge word…</button>{extra}",
            "nyi__actions",
            task=n.ref,
            rid=new_request_id(),
        ) + _posture_tag(n.project, ctx)
    else:
        act = f"<div class=nyi__actions>{extra}</div>" if extra else ""
    return row, dot, badge, summary, act


_QUEUED_SHOWN = 4  # queued tickets listed per project before "+N more"


def _row_parts(r: Row, href: str, ctx: Ctx) -> tuple[str, str, str, str, str]:
    """A queued ticket's lane parts: row class, tag, tag class, summary and actions."""
    if r.hold:
        cls, tag, status = "nyi--gate", "held", f"Held: {ctx.plain(r.hold)}"
    elif r.blocked_by:
        cls, tag = "nyi--blocked", "blocked"
        status = "Blocked by " + ", ".join(esc(b) for b in r.blocked_by)
    else:
        cls, tag, status = "nyi--queued", "", "Queued, nothing blocking it"
    summary = f"<b>{ctx.plain(r.title or r.id)}</b> — {status}"
    act = (
        f"<div class=nyi__actions><a class='btn btn--deny' href='{esc(href)}'>"
        "Open project ↗</a></div>"
    )
    return cls, tag, "nyx__tag--hold" if r.hold else "", summary, act


def _nyx(
    *,
    group: BacklogGroup,
    item_id: str,
    title: str,
    cls: str,
    dot: str,
    tag: str,
    tag_cls: str,
    summary: str,
    act: str,
    primary: bool,
    ctx: Ctx,
) -> str:
    """One lane item. The same element is a compact row or the expanded card (``is-primary``):
    the page script swaps which one is the group's card when a row is picked."""
    href = "/p/" + quote(group.project, safe="")
    text = ctx.descriptions.get(f"{group.project}/{item_id}")
    desc_url = "/describe?" + urlencode({"p": group.project, "id": item_id})
    desc = (
        "<p class=nyx__desc data-ready=1 tabindex=0 role=button aria-haspopup=dialog>"
        f"{esc(text)}</p>"
        if text
        else "<p class=nyx__desc hidden></p>"
    )
    badge = f"<span class='nyx__tag {tag_cls}'>{esc(tag)}</span>" if tag else ""
    state = " is-primary" if primary else ""
    return (
        f"<div class='nyx {cls}{state}' data-item='{esc(item_id)}' data-desc='{esc(desc_url)}'>"
        f"<div class=nyx__bar><a class=nyx__head href='{esc(href)}' title='{esc(title or item_id)}'>"
        f"<span class='state-dot state-dot--{dot}'></span><span class=nyx__id>{esc(item_id)}</span>"
        f"<span class=nyx__title>{esc(title or item_id)}</span>{badge}</a>"
        f"<a class=nyx__open href='{esc(href)}' aria-label='Open {esc(group.project)}'>"
        "open ↗</a></div>"
        f"<div class=nyx__more><div class=nyi__summary>{summary}</div>{desc}{act}</div></div>"
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
    return f"<a class='btn btn--deny' href='{esc(href)}'>Open project ↗</a>"


def _sheet_row(href: str, item_id: str, title: str, tag: str) -> str:
    badge = f"<span class=rv__reply>{esc(tag)}</span>" if tag else ""
    return (
        f"<a class=rv href='{esc(href)}'><span class=rv__title>{esc(title or item_id)}</span>"
        f"<span class=rv__proj>{esc(item_id)}</span>{badge}<span class=rv__go>↗</span></a>"
    )


def _items_list(g: BacklogGroup | None, ctx: Ctx) -> str:
    """Every item of a project's Needs-you slice (held first, then queued), for a sheet."""
    if g is None or not g.count:
        return "<p class=sheet__empty>Nothing waiting on you or queued.</p>"
    href = "/p/" + quote(g.project, safe="")
    rows = [
        _sheet_row(href, n.ref, n.title or n.text, _LANE.get(n.kind, ("", "", n.kind))[2])
        for n in g.waiting
    ]
    rows += [_sheet_row(href, r.id, r.title, _row_parts(r, href, ctx)[1]) for r in g.queued]
    return f"<div class=sheet__list>{''.join(rows)}</div>"


def _group(g: BacklogGroup, ctx: Ctx, sid: str) -> str:
    """One project's slice: the first held item is the card, the rest are rows; picking a row
    (page script) swaps it into the card slot. Without the script every row opens the project.
    "+N more" opens a sheet listing the whole slice (without the script, the project page)."""
    href = "/p/" + quote(g.project, safe="")
    shown = g.queued[:_QUEUED_SHOWN]
    rest = len(g.queued) - len(shown)
    more = (
        f"<a class=nyq__more href='{esc(href)}' data-sheet-open={sid} aria-haspopup=dialog "
        f"aria-controls={sid}>+{rest} more</a>"
        + _sheet(
            sid,
            f"{ctx.name_html(g.project)} · {g.count}",
            _items_list(g, ctx),
            f"Close {ctx.show(g.project)}",
            _open_project(href),
        )
        if rest
        else ""
    )
    waiting = f"<span class=nyg__wait>{len(g.waiting)} waiting on you</span>" if g.waiting else ""
    items = []
    for i, n in enumerate(g.waiting):
        cls, dot, badge, summary, act = _hold_parts(n, ctx)
        items.append(
            _nyx(
                group=g,
                item_id=n.ref,
                title=n.title or n.text,
                cls=cls,
                dot=dot,
                tag=badge,
                tag_cls="nyx__tag--hold",
                summary=summary,
                act=act,
                primary=i == 0,
                ctx=ctx,
            )
        )
    for r in shown:
        cls, tag, tag_cls, summary, act = _row_parts(r, href, ctx)
        items.append(
            _nyx(
                group=g,
                item_id=r.id,
                title=r.title,
                cls=cls,
                dot="gated" if r.hold else "idle",
                tag=tag,
                tag_cls=tag_cls,
                summary=summary,
                act=act,
                primary=False,
                ctx=ctx,
            )
        )
    return (
        f"<section class=nyg data-group='{esc(g.project)}' data-open='{esc(href)}'>"
        f"<a class=nyg__head href='{esc(href)}'>"
        f"<span class=nyg__name>{ctx.name_html(g.project)}</span>"
        f"<span class=nyg__mate>{'second mate' if g.mate else 'first mate'}</span>{waiting}"
        f"<span class=nyg__count>{g.count}</span></a>"
        f"<div class=nyg__items>{''.join(items)}</div>{more}</section>"
    )


def _lane(desk: Desk, ctx: Ctx) -> str:
    """ "Needs you": the backlog across every project, grouped by project (not worker chatter)."""
    head = (
        "<div class=nyl__head><span class=nyl__glyph aria-hidden=true>◆</span>"
        f"<h2 class=nyl__title>Needs you</h2><span class=nyl__count>{desk.backlog_count}</span></div>"
    )
    if not desk.backlog:
        n = desk.loops_running
        return (
            f"<section class='nyl nyl--calm'>{head}<div class=calm>"
            "<div class=calm__check aria-hidden=true>✓</div>"
            f"<p class=calm__line>✓ all clear · {n} loop{'' if n == 1 else 's'} running</p>"
            "<p class=calm__sub>backlog is empty</p></div></section>"
        )
    groups = "".join(_group(g, ctx, f"nys-{i}") for i, g in enumerate(desk.backlog))
    return f"<section class=nyl>{head}<div class=nyl__body>{groups}</div></section>"


_DOT = {"blocked": "error", "running": "active", "idle": "idle"}


def _target(p: Project, ctx: Ctx) -> tuple[str, str]:
    """(delegate-bar label, project field) for a focused card."""
    if p.name == NO_PROJECT:
        return "first mate", ""
    return f"{'second mate' if p.mate else 'first mate'} · {ctx.show(p.name)}", p.name


def _card_text(p: Project) -> tuple[Glance, str, str]:
    """A card's glance, its activity line (HTML) and its progress label."""
    g = glance(p)
    prog = "done" if g.total and g.done == g.total else f"{g.done} / {g.total} tasks"
    now = f"<b>{esc(g.lead)}</b> — {esc(g.now)}" if g.lead else esc(g.now)
    return g, now, prog


def _card(p: Project, selected: bool, ctx: Ctx, sid: str) -> str:
    """A project card: a tap focuses it (retargets the delegate bar); a tap on the focused card
    opens its detail sheet ``sid``. Without the script the focused card opens the project."""
    g, now, prog = _card_text(p)
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
        f"<span class=pc__top><span class='state-dot state-dot--{_DOT[g.status]}'></span>"
        f"<span class=pc__name>{ctx.name_html(p.name)}</span>"
        f"<span class=pc__mae>{'second mate' if p.mate else 'first mate'}</span>"
        f"<span class=pc__status>{g.status}</span></span>"
        f"<p class=pc__now>{now}</p>"
        f"<span class=pc__prog><span class=pc__track><i style='width:{pct}%'></i></span>{prog}"
        "<span class=pc__open>· tap for details</span></span></a>"
    )


def _card_sheet(p: Project, group: BacklogGroup | None, ctx: Ctx, sid: str) -> str:
    """The focused card's detail: status, activity, progress and its Needs-you items."""
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
    label, field = _target(focus, ctx) if focus else ("first mate", "")
    nxt = "/?focus=" + quote(focus.name, safe="") if focus else "/"
    form = ctx.form(
        "delegate",
        f"<span class=dbar__target data-target>→ {esc(label)}</span>"
        "<input type=text class=dbar__in name=text required maxlength=4000 placeholder='Describe a goal…' "
        "aria-label='Delegate a goal'><button class=dbar__go>Delegate</button>",
        "dbar",
        rid=new_request_id(),
        project=field,
        next=nxt,
    )
    if not form:
        return stamp
    hidden = " hidden" if field else ""
    return (
        "<div class=dbar-wrap><p class=dbar-flash data-flash role=status hidden></p>"
        f"<div class=dbar-info><p class=dbar-note>Delegate to <b data-tname>{esc(label)}</b>"
        f"<span data-nofocus{hidden}> · no project focus</span>"
        f" · tapping a project card retargets this bar</p>{stamp}</div>{form}</div>"
    )


_REVIEWS_SHOWN = 10


def _review_form(action: str, label: str, ctx: Ctx, cls: str = "rv__x", **hidden: str) -> str:
    """A POST form that ends review sessions. The page script turns the first tap into an
    in-place confirm ("Tap again"); without it the server shows a confirm page."""
    btn = f"<button class='{cls}' data-label='{esc(label)}'>{esc(label)}</button>"
    form = ctx.form(action, btn, rid=new_request_id(), **hidden)
    return form.replace("<form ", "<form data-rvclose ", 1) if form else ""


def _review(r: Review, ctx: Ctx) -> str:
    href = f"{ctx.board_url}/session/{r.key}"
    reply = "<span class=rv__reply>reply</span>" if r.reply else ""
    done = "<span class=rv__done>looks done</span>" if r.stale else ""
    where = f"<span class=rv__proj>{esc(ctx.show(r.project))}</span>" if r.project else ""
    close = _review_form("review-close", "Close", ctx, key=r.key) if ctx.can_close_reviews else ""
    return (
        f"<div class='rvrow{' rvrow--stale' if r.stale else ''}'>"
        f"<a class=rv href='{esc(href)}' target=_blank rel='noopener noreferrer'>"
        f"<span class=rv__title>{esc(r.title)}</span>{where}{done}{reply}<span class=rv__go>↗</span>"
        f"</a>{close}</div>"
    )


def _reviews(reviews: list[Review], ctx: Ctx) -> str:
    """Open Lavish review sessions as tap targets that open on the tailnet board URL.

    The first few sit in a scrollable box; "+N more" is always its last row and opens a
    sheet listing every session (the same unread-replies-first order). Each row has a Close
    button; pages that look done (old, or beyond the newest eight) are tagged, nudged about
    above the list, and closed together by "Close all old".
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
    if stale and can:
        word = "page looks" if stale == 1 else "pages look"
        nudge = (
            f"<div class=rvs__nudge role=status><span>{stale} {word} done: close "
            f"{'it' if stale == 1 else 'them'}?</span>"
            "<button type=button class=rv__review data-sheet-open=rvs-sheet aria-haspopup=dialog "
            "aria-controls=rvs-sheet>Review</button>"
            f"{_review_form('review-close-old', f'Close {stale}', ctx, 'rv__x rv__x--bulk')}</div>"
        )
    if rest > 0 or (stale and can):
        bulk = (
            _review_form("review-close-old", "Close all old", ctx, "rv__x rv__x--bulk")
            if stale and can
            else ""
        )
        sheet = _sheet(
            "rvs-sheet",
            f"Review pages · {n}",
            f"<div class=sheet__list>{''.join(_review(r, ctx) for r in reviews)}</div>",
            "Close review pages",
            bulk,
        )
    return (
        f"<section class=rvs><p class=sec-label>Review pages · {n}</p>{nudge}"
        f"<div class=rv__box><div class=rv__list tabindex=0 role=region "
        f"aria-label='Open review pages'>{shown}</div>{more}</div>{sheet}</section>"
    )


def render_home(
    snap: Snapshot,
    desk: Desk | None,
    ctx: Ctx,
    focus: str | None = None,
    reviews: list[Review] | None = None,
) -> str:
    attrs = " data-refresh=30000"
    if desk is None:
        return _page("Hive desk", f"<h1>Hive desk</h1>{_banner(snap, ctx)}", "desk", attrs, ctx)
    if focus not in desk.projects:
        focus = desk.default_focus()
    groups = {g.project: g for g in desk.backlog}
    cards = details = ""
    for i, p in enumerate(desk.projects.values()):
        cards += _card(p, p.name == focus, ctx, f"pcs-{i}")
        details += _card_sheet(p, groups.get(p.name), ctx, f"pcs-{i}")
    more = "".join(
        f"<p class=stamp>+{n} more owned by {esc(owner)}, not shown</p>"
        for owner, n in desk.more.items()
    )
    projects = (
        f"<section class=pcsec><p class=sec-label>Projects · tap to focus</p>"
        f"<div class=pcs>{cards}</div>{details}{more}</section>"
        if cards
        else f"<section class=pcsec><p class=sec-label>No projects yet</p>{more}</section>"
    )
    stamp = f"<p class=stamp>Updated {ctx.time(desk.generated)}</p>" if desk.generated else ""
    body = (
        f"<div class=screen data-stack><div class=land><div class=land__col>{_lane(desk, ctx)}"
        f"</div><div class=land__col>{projects}{_reviews(reviews or [], ctx)}</div></div>"
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


def _crew(c: Crew, ctx: Ctx) -> str:
    controls = ctx.form(
        "control",
        "<button class='quiet inline' name=verb value=interrupt>Interrupt…</button>"
        "<button class='quiet inline' name=verb value=relaunch>Relaunch…</button>",
        task=c.id,
    )
    return (
        f"<div class=row><span class=tag>{esc(c.state)}</span><strong>{esc(c.title or c.id)}"
        f"</strong> <span class=mute>{esc(c.detail)}</span>"
        f"<span class=sub>{esc(c.id)} · {esc(c.kind)} · {esc(c.harness)} · "
        f"<a href='/w/{esc(quote(c.id, safe=''))}'>Watch live</a></span>{controls}</div>"
    )


def _ticket_edit(r: Row, project: str, ctx: Ctx) -> str:
    if r.state == "done":
        return ""
    options = "".join(f"<option>{esc(f)}</option>" for f in TICKET_FIELDS)
    return ctx.form(
        "ticket",
        f"<details><summary>Request an edit</summary><label>Field</label>"
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
        "<details><summary>New ticket</summary><label>Title</label>"
        "<input type=text name=title required maxlength=200><label>Details</label>"
        "<textarea name=text maxlength=4000></textarea>"
        "<button>Send create request</button></details>",
        mode="create",
        project=project,
        rid=new_request_id(),
    )


def render_project(
    name: str,
    snap: Snapshot,
    project: Project | None,
    ctx: Ctx,
) -> str:
    attrs = " data-refresh=30000"
    if project is None:
        body = _banner(snap, ctx) if not snap.ok else "<p>Unknown project.</p>"
        return _page(name, f"<p><a href='/'>← Desk</a></p>{body}", "", attrs, ctx)
    needs = (
        "".join(_need(n, False, ctx) for n in project.needs_you)
        or "<p class=mute>Nothing needs you.</p>"
    )
    titles = {r.id: r.title for r in project.rows}
    rows = []
    for r in project.rows:
        extra = [r.id]
        if r.owner and r.owner != "main":
            extra.append(f"owner: {esc(r.owner)}")
        if r.hold:
            extra.append(f"held: {esc(r.hold)}")
        if r.blocked_by:
            extra.append("blocked by " + esc(", ".join(titles.get(b) or b for b in r.blocked_by)))
        pr = ctx.link(r.pr_url)
        if pr:
            extra.append(f'<a href="{esc(pr)}" rel="noopener noreferrer">PR</a>')
        sub = f"<span class=sub>{' · '.join(extra)}</span>"
        rows.append(
            f"<div class=row><span class=tag>{esc(r.state.replace('_', ' '))}</span>"
            f"<strong>{esc(r.title or r.id)}</strong>{sub}"
            f"{_ticket_edit(r, project.name, ctx)}</div>"
        )
    crews = "".join(_crew(c, ctx) for c in project.crews) or "<p class=mute>No live crews.</p>"
    return _page(
        f"{ctx.show(name)} · Hive desk",
        f"<p><a href='/'>← Desk</a></p><h1>{ctx.name_html(name)}</h1>"
        f"<h2>Needs you ({len(project.needs_you)})</h2>"
        f"{needs}<h2>Crews</h2>{crews}<h2>Backlog ({len(project.rows)})</h2>"
        f"{_new_ticket(project.name, ctx)}{''.join(rows) or '<p class=mute>Empty.</p>'}",
        "",
        attrs,
        ctx,
    )


_KIND_LABEL = {
    "chat": "",
    "ticket": "ticket request",
    "merge": "merge word",
    "decision": "answer",
    "delegate": "delegated",
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
        kind = f"<span class=tag>{esc(_KIND_LABEL[r.kind])}</span>" if _KIND_LABEL[r.kind] else ""
        rid = f" data-rid='{esc(r.request_id)}'" if r.request_id else ""
        msgs.append(
            f"<div class='msg me'{rid}>{kind}<div class=bubble>{esc(r.body)}</div>"
            f"<span class=meta>{ctx.time(r.at)} · {esc(_STATE_LABEL[r.state])}</span></div>"
        )
        if r.reply is not None:
            msgs.append(
                f"<div class='msg fm'><span class=tag>first mate</span>"
                f"<div class=bubble>{esc(r.reply)}</div>"
                f"<span class=meta>{ctx.time(r.reply_at)}</span></div>"
            )
    out = "".join(msgs) or "<p class='mute empty'>No messages yet. Say hello below.</p>"
    if view.omitted:
        out = "<p class=mute>Older entries omitted: " + esc("; ".join(view.omitted)) + "</p>" + out
    return out


def render_watch(crew: Crew, ctx: Ctx) -> str:
    return _page(
        f"{crew.id} · Hive desk",
        f"<p><a href='/'>← Desk</a></p><h1>{esc(crew.title or crew.id)}</h1>"
        f"<p class=mute><span class=tag>{esc(crew.state)}</span>{esc(crew.detail)} · "
        f"{esc(crew.id)} · {esc(crew.harness)}</p>"
        "<p class=mute id=tail-status role=status>Loading…</p>"
        "<pre class=tail id=tail aria-label='Recent output' aria-live=off></pre>",
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
        "required maxlength=4000 enterkeyhint=send placeholder='Ask, delegate, or steer…'>"
        "</textarea><button>Send</button>",
        "chatf",
        rid=new_request_id(),
    )
    return _page(
        "Chat · Hive desk",
        f"<h1>Chat with the first mate</h1>{status}"
        f"<div class=thread id=thread aria-live=polite>{_thread(view, ctx)}</div>"
        "<button id=jump type=button hidden>New messages \u2193</button>"
        f"<div class=dock>{form}</div>",
        "chat",
        " data-poll=4000",
        ctx,
    )


def _cfg_note(error: str) -> str:
    return f"<p class=unk>unknown: {esc(error)}</p>"


def _profile_line(p: fmconfig.Profile) -> str:
    parts = (p.harness, p.model or "harness default model", p.effort or "default effort")
    return " · ".join(esc(x) for x in parts)


def render_config(cfg: fmconfig.FmConfig, ctx: Ctx) -> str:
    """Firstmate's merge posture and worker profiles, read-only. A source that cannot be
    read shows ``unknown`` with the reason; the page itself never fails."""
    pr = cfg.projects
    if pr.error:
        merge = _cfg_note(pr.error)
    else:
        rows = [
            f"<tr><td>{esc(n)}</td><td>{esc(p.mode)}</td><td>{'on' if p.yolo else 'off'}</td></tr>"
            for n, p in sorted(pr.postures.items())
        ] + [
            f"<tr><td>{esc(n)}</td><td colspan=2 class=unk>unknown: {esc(why)}</td></tr>"
            for n, why in sorted(pr.ambiguous.items())
        ]
        merge = (
            "<div class=tblw><table><thead><tr><th>Project</th><th>Delivery</th><th>Yolo</th>"
            f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
        )
    d = cfg.dispatch
    if d.error:
        profiles = _cfg_note(d.error)
    else:
        items = [
            f"<li><div class=when title='{esc(r.when)}'>{esc(r.when)}</div>"
            f"<div class=prof>{'<br>'.join(_profile_line(p) for p in r.profiles)}</div></li>"
            for r in d.rules
        ]
        if d.default:
            items.append(
                "<li><div class=when>Anything else (default)</div>"
                f"<div class=prof>{'<br>'.join(_profile_line(p) for p in d.default)}</div></li>"
            )
        profiles = f"<ul>{''.join(items)}</ul>"
    harness = _cfg_note(cfg.harness.error) if cfg.harness.error else esc(cfg.harness.text)
    if cfg.harness.absent:
        harness = "not set"
    perm = _cfg_note(cfg.permission.error) if cfg.permission.error else esc(cfg.permission.text)
    if cfg.permission.absent:
        perm += " (default)"
    gate = cfg.workers_ready
    ready = (
        f"<p class=unk>Workers will not start: {esc(gate)}</p>"
        if gate
        else "<p class=mute>Workers can start from these settings.</p>"
    )
    return _page(
        "Config · Hive desk",
        "<div class=cfg><h1>Firstmate config</h1>"
        "<p class=mute>Read from firstmate on each load. Hive keeps no copy and cannot edit it.</p>"
        f"<h2>Merge posture</h2>{merge}"
        f"<h2>Worker profiles</h2>{profiles}"
        f"<h2>Worker launch</h2><p class=kv><span>Crew harness: {harness}</span>"
        f"<span>Claude permission mode: {perm}</span></p>{ready}</div>",
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


def _mrow(state: str, urgent: bool, label: str, head: str, detail: list[str], side: str) -> str:
    """One line of the Machine page: a state chip (a word, not colour alone), text, a side note."""
    state = _mstate(state)
    cls = f"mrow mrow--{state}" + (" mrow--urgent" if urgent else "")
    line = " · ".join(d for d in detail if d)
    return (
        f"<li class='{cls}'><span class='mstate mstate--{state}'>{_MSTATE[state]}</span>"
        f"<div class=mrow__body><div class=mrow__top><b>{esc(label)}</b>"
        + (f"<span class=mrow__head>{esc(head)}</span>" if head else "")
        + "</div>"
        + (f"<p class=mrow__detail>{line}</p>" if line else "")
        + f"</div>{side}</li>"
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
            [esc(r.detail), _moment(r.moment, ctx)],
            f"<span class=mrow__side>checked {ctx.time(_iso(r.checked_at))}</span>"
            if r.checked_at
            else "",
        )
        for r in view.rows
    )
    risks = "".join(
        _mrow(
            k.state,
            k.urgent,
            k.label,
            "",
            [_moment(k.moment, ctx, lead=True), esc(k.detail)],
            f"<span class='mrow__side mrow__due'>{esc(k.due)}</span>",
        )
        for k in view.risks
    )
    counts = {
        s: sum(_mstate(r.state) == s for r in view.rows) for s in ("down", "warn", "unknown", "ok")
    }
    summary = "".join(
        f"<li class='mstate mstate--{s}'>{n} {_MSTATE[s]}</li>" for s, n in counts.items() if n
    )
    stamp = (
        f"<p class=stamp>Checked {ctx.time(_iso(view.checked_at))}</p>" if view.checked_at else ""
    )
    note = esc(view.note) or "Nothing to report."
    a = view.alarm
    alarm = (
        "<h2 class=sec-label>Gone-dark alarm</h2><ul class=mlist>"
        + _mrow(a.state, False, a.label, a.headline, [esc(a.detail), _moment(a.moment, ctx)], "")
        + "</ul>"
        if a is not None
        else ""
    )
    return _page(
        "Machine · Hive desk",
        "<div class=mach><div class=mach__head><h1>Machine</h1>"
        f"<ul class=mach__sum aria-label='Chain summary'>{summary}</ul></div>"
        "<div class=mach__lead><p class=mute>Read-only. Every link has to hold to reach firstmate. "
        "Renew a sign-in on the <a href='/signins'>Sign-ins page</a>.</p>"
        f"{stamp}"
        + (
            "<p class=mach__term><a href='/terminal'>Open firstmate’s terminal</a></p>"
            if ctx.terminal
            else ""
        )
        + "</div>"
        "<div class=mach__cols><section class=mach__col><h2 class=sec-label>The chain</h2>"
        f"<ol class=mlist>{rows}</ol>{alarm}</section>"
        "<section class=mach__col><h2 class=sec-label>Upcoming risks</h2>"
        + (f"<ul class=mlist>{risks}</ul>" if risks else f"<p class=mute>{note}</p>")
        + "</section></div></div>",
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
    """One fix button: what it does, the exact command or helper verb, how long it takes."""
    a = card.action
    cls = "rq" + (" rq--down" if a.down else "") + (" rq--off" if card.why_not else "")
    body = (
        f"<div class=rq__body><div class=rq__top><b>{esc(a.label)}</b>"
        f"<span class=rq__took>{esc(a.took)}</span></div>"
        f"<p class=rq__what>{esc(a.plain)}</p><code class=rq__cmd>{esc(a.command())}</code>"
        + (f"<p class=rq__off>{esc(card.why_not)}</p>" if card.why_not else "")
        + "</div>"
    )
    form = ""
    if not card.why_not:
        pick = ""
        if a.choice:
            fid = f"rq-{a.key}"
            opts = "".join(
                f"<option value={v}{' selected' if v == a.default else ''}>{esc(a.short(v))}</option>"
                for v in a.choices
            )
            pick = f"<label for={fid}>For</label><select id={fid} name=n>{opts}</select>"
        cls_b = " class=danger" if a.down else ""
        form = ctx.form(
            "run", f"{pick}<button{cls_b} type=submit>Run</button>", always=True, do=a.key
        )
    return f"<li class='{cls}'>{body}{form}</li>"


def _run_row(r: runqueue.Run, owner: str, ctx: Ctx) -> str:
    row, chip, word = _RUN_RESULT.get(r.result, _RUN_RESULT[runqueue.UNKNOWN])
    who = "you" if r.who == owner else machine.redact(r.who or "-", 40)
    took = _secs(r.duration_s)
    detail = " · ".join(x for x in (esc(r.detail), f"took {took}" if took else "") if x)
    return (
        f"<li class='mrow mrow--{row} rqrun'><span class='mstate mstate--{chip}'>{word}</span>"
        f"<div class=mrow__body><div class=mrow__top><b>{esc(r.label)}</b>"
        f"<span class=mrow__head>by {esc(who)}</span></div>"
        + (f"<p class=mrow__detail>{detail}</p>" if detail else "")
        + f"</div><span class=mrow__side>{ctx.time(_iso(r.start))}</span></li>"
    )


def _helper_note(state: str) -> str:
    if state == runqueue.READY:
        return (
            "<p class=rqnote>Through the admin helper only: each button writes one request "
            "line for it and starts its task, and its answer is shown below.</p>"
        )
    more = (
        "It has not answered the desk yet. If the sitting is done, tap Check the admin "
        "helper; the other Windows buttons are offered once it answers."
        if state == runqueue.UNPROVEN
        else "The one sitting at the PC sets it up. Until then no Windows button is offered."
    )
    return (
        f"<div class=banner role=status><p><b>{esc(runqueue.NOT_SET_UP)}</b></p>"
        f"<p class=mute>{esc(more)} ({esc(runqueue.SITTING)})</p></div>"
    )


def render_run(view: runqueue.RunView, ctx: Ctx) -> str:
    """The Run page: fixed fix buttons, then the last runs. Nothing runs from a page load;
    each button needs a confirm step (two for the ones that take things down)."""
    linux = "".join(_run_card(c, ctx) for c in view.cards if c.action.side == "linux")
    windows = "".join(_run_card(c, ctx) for c in view.cards if c.action.side == "windows")
    windows += "".join(
        "<li class='rq rq--off'><div class=rq__body><div class=rq__top>"
        f"<b>{esc(label)}</b></div><p class=rq__what>{esc(why)}</p>"
        "<p class=rq__off>Not offered.</p></div></li>"
        for label, why in view.not_offered
    )
    rows = "".join(_run_row(r, view.owner, ctx) for r in view.runs)
    runs = f"<ol class=mlist>{rows}</ol>" if rows else "<p class=mute>No runs yet.</p>"
    return _page(
        "Run · Hive desk",
        "<div class=runq><div class=mach__head><h1>Run</h1></div>"
        "<div class=mach__lead><p class=mute>Fixed fixes only. A tap shows exactly what will "
        "run; it runs only after you tap again, and every run is logged.</p></div>"
        "<div class=runq__cols><section class=runq__col>"
        "<h2 class=sec-label>This desk · Linux</h2>"
        "<p class=rqnote>Runs as you inside WSL, never as root.</p>"
        f"<ol class=rqlist>{linux}</ol>"
        "<h2 class=sec-label>The PC · Windows</h2>"
        f"{_helper_note(view.helper)}<ol class=rqlist>{windows}</ol></section>"
        f"<section class=runq__col><h2 class=sec-label>Last {runqueue.SHOWN_RUNS} runs</h2>"
        f"{runs}</section></div></div>",
        "run",
        f" data-refresh={RUN_REFRESH_MS} data-run",
        ctx,
    )


SIGNINS_REFRESH_MS = 30000  # an open Sign-ins page re-reads its rows this often


def _signin_poll(ctx: Ctx, token: str, visible: bool) -> str:
    """The form the page asks "how is the sign-in going" with. The starting page's token goes
    in the request body, never in a link. Hidden on the page the script drives."""
    button = "<button class=quiet type=submit>Check again</button>" if visible else ""
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
        return (
            f"<div class='sgf {cls}' role=status><p class=sgf__title>{name} sign-in{when}</p>"
            f"<p>{esc(v.message)}{good}</p></div>"
        )
    cancel = ""
    if v.phase != signins.STOPPING:
        label = "Cancel" if v.mine else "Stop that sign-in"
        cancel = ctx.form(
            "signin-cancel",
            f"<button class=quiet type=submit>{label}</button>",
            always=True,
            flow=v.token,
        )
    foot = (
        f"<div class=sgf__foot><p class=mute>Stops by itself at {_time_of_day(v.ends_at, ctx)}.</p>"
        f"{cancel}</div>"
    )
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
            "<li>Open the sign-in page and sign in as yourself."
            f'<a class=sgf__link href="{esc(v.link)}" target=_blank rel="noopener noreferrer">'
            f"Open the {name} sign-in page ↗</a><span class=sgf__host>{host}</span></li>"
        )
        if v.code:
            steps += (
                "<li>Enter this one-time code there.<br>"
                f"<strong class=sgf__code>{esc(v.code)}</strong></li>"
                f"<li>Come back here. This page shows when {name} has signed the PC in.</li>"
            )
        if v.takes_code:
            box = (
                "<input type=text name=code id=sgcode autocomplete=off autocapitalize=off "
                f"autocorrect=off spellcheck=false maxlength={signins.MAX_CODE} required>"
                "<button type=submit>Send code</button>"
            )
            steps += (
                f"<li><label for=sgcode>{name} then shows a code. Paste it here.</label>"
                + ctx.form("signin-code", box, "sgf__paste", always=True, flow=v.token)
                + f"{note}</li>"  # what the vendor said about the last code, under the box
            )
            note = ""
        body = (
            f"<ol class=sgf__steps>{steps}</ol>"
            "<p class=mute>The link works once and is shown only on this page.</p>"
        )
    ends = f" data-ends='{int(v.ends_at * 1000)}'" if v.mine and v.ends_at else ""
    return (
        f"<div class='sgf sgf--live'{ends}><p class=sgf__title>{name} sign-in"
        f"<span class=mute>in progress</span></p>{body}{note}{foot}</div>"
    )


def _sgrow(r: signins.SignIn, ctx: Ctx, busy: bool) -> str:
    """One sign-in: the Machine page's state chip and wording, plus Renew or why not."""
    state = _mstate(r.state)
    cls = f"mrow sgrow mrow--{state}" + (" mrow--urgent" if r.urgent else "")
    line = " · ".join(d for d in (esc(r.detail), _moment(r.moment, ctx)) if d)
    why, act = esc(r.why_not), ""
    if r.help_url:
        why += (
            f' <a href="{esc(r.help_url)}" target=_blank rel="noopener noreferrer">'
            f"{esc(r.help_label)} ↗</a>"
        )
    if r.renewable and busy:
        why = "A sign-in is running. Renew is back when it ends."
    elif r.renewable:
        act = ctx.form("signin", "<button type=submit>Renew</button>", always=True, provider=r.key)
    return (
        f"<li class='{cls}'><span class='mstate mstate--{state}'>{_MSTATE[state]}</span>"
        f"<div class=mrow__body><div class=mrow__top><b>{esc(r.label)}</b>"
        f"<span class=mrow__head>{esc(r.headline)}</span></div>"
        + (f"<p class=mrow__detail>{line}</p>" if line else "")
        + (f"<p class=sgrow__why>{why}</p>" if why else "")
        + f"</div><div class=sgrow__act>{act}</div></li>"
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
    summary = "".join(
        f"<li class='mstate mstate--{s}'>{n} {_MSTATE[s]}</li>" for s, n in counts.items() if n
    )
    stamp = (
        f"<p class=stamp>Checked {ctx.time(_iso(panel.checked_at))}</p>" if panel.checked_at else ""
    )
    return _page(
        "Sign-ins · Hive desk",
        "<div class=mach><div class=mach__head><h1>Sign-ins</h1>"
        f"<ul class=mach__sum aria-label='Sign-in summary'>{summary}</ul></div>"
        "<div class=mach__lead><p class=mute>Renew a sign-in from here before it runs out. "
        f"<a href='/machine'>Back to Machine</a></p>{stamp}</div>"
        + _signin_box(panel.flow, ctx)
        + _signin_poll(ctx, "", visible=False)
        + f"<ul class=mlist>{rows}</ul></div>",
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
        "<div class=mach><h1>Sign-ins</h1>"
        + _signin_box(view, ctx)
        + poll
        + "<p><a href='/signins'>Back to Sign-ins</a></p></div>",
        "signins",
    )


def render_confirm(title: str, detail: str, action: str, ctx: Ctx, hidden: dict[str, str]) -> str:
    """Step-up page: nothing runs until this form is posted back with ``step``."""
    inner = (
        f"<p>{esc(detail)}</p>"
        "<button class=danger name=confirm value=1>Confirm</button> "
        f"<a href='{esc(ctx.nxt)}'>Cancel</a>"
    )
    form = ctx.form(action, inner, **hidden)
    return _page(f"{title} · Hive desk", f"<h1>{esc(title)}</h1><div class=banner>{form}</div>")


def render_result(title: str, message: str, nxt: str, ok: bool) -> str:
    cls = "banner ok" if ok else "banner"
    return _page(
        f"{title} · Hive desk",
        f"<h1>{esc(title)}</h1><div class='{cls}' role=status>{esc(message)}</div>"
        f"<p><a href='{esc(nxt)}'>Back</a></p>",
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
body.termpage{position:fixed;top:0;left:0;width:100%;height:100vh;height:var(--app-h,100dvh);overflow:hidden;
display:flex;flex-direction:column}
.termpage main{flex:1;min-height:0;width:100%;max-width:1200px;display:flex;flex-direction:column;
padding:0 0 calc(8px + env(safe-area-inset-bottom))}
.term{flex:1;min-height:0;display:flex;flex-direction:column;gap:8px}
.term__head{display:flex;flex-wrap:wrap;align-items:baseline;gap:0 12px}
.term__head h1{margin:8px 0 0;font-size:1.15rem}.term__head p{margin:0}
.tbar{flex:none;display:flex;flex-wrap:wrap;align-items:center;gap:6px 10px;min-height:58px;
padding:6px 6px 6px 8px;background:var(--card);border:1.5px solid var(--line);border-radius:12px}
.tbar[data-s=mine]{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.tstate{flex:none;display:inline-flex;align-items:center;min-height:30px;padding:0 12px;border-radius:999px;
border:1.5px solid var(--rule-soft);font:700 11px/1 var(--font-mono);letter-spacing:.08em;text-transform:uppercase;
color:var(--ink-3);white-space:nowrap}
.tstate--ro{border-color:var(--sage);color:var(--sage);background:var(--sage-soft)}
.tstate--mine{border-color:var(--accent);background:var(--accent);color:#fff}
.tstate--other{border-color:var(--amber);color:var(--amber);background:var(--amber-soft)}
.tstate--down{border-color:var(--accent);color:var(--accent);background:var(--accent-soft)}
.tstate--wait{border-color:var(--honey);color:var(--ochre);background:var(--honey-soft)}
.tbar__who{flex:1 1 240px;min-width:0;font-size:.86rem;line-height:1.35;color:var(--ink-2);overflow-wrap:anywhere}
.tbar__btns{flex:none;display:flex;gap:6px;margin-left:auto}.tbar__btns:empty{display:none}
.tbar button{min-height:44px;padding:0 14px;font:700 12px var(--font-mono);letter-spacing:.02em}
.tbar button[hidden]{display:none}.tbar button:disabled{opacity:.45;cursor:not-allowed;box-shadow:none}
.term button.is-armed{box-shadow:0 0 0 3px var(--accent-soft)}
.term .act-warn{margin:0}.term .act-warn[hidden]{display:none}
.tgate{flex:1;min-height:0;overflow:auto;display:flex;align-items:flex-start;padding:4px 0 16px}
.tgate[hidden],.tlive[hidden]{display:none}
.tgate .card{width:100%;max-width:560px}
.tgate h2{margin:0 0 6px}.tgate p{margin:0 0 8px}
.tgate ul{margin:0 0 12px;padding-left:20px;color:var(--ink-2);font-size:.92rem}.tgate li{margin:3px 0}
.tgate form{margin:0}.tgate button{min-height:48px;padding:0 20px;font-weight:700}
.tlive{flex:1;min-height:0;display:flex;flex-direction:column;gap:8px}
.twrap{position:relative;flex:1;min-height:140px;display:flex}
.tframe{flex:1;min-width:0;overflow:auto;-webkit-overflow-scrolling:touch;overscroll-behavior:contain;
padding:8px;background:#16140f;border:1.5px solid var(--rule-soft);border-radius:12px}
.tframe .xterm{display:inline-block;vertical-align:top}
/* The desk's textarea min-height would stretch xterm's hidden input below the screen. */
.tframe .xterm-helper-textarea{min-height:0}
.tframe__msg{position:absolute;inset:1.5px;display:flex;align-items:center;justify-content:center;margin:0;
padding:24px;border-radius:11px;background:#16140f;color:#d6cfbf;font:600 14px/1.5 var(--font-sans);text-align:center}
.tframe__msg[hidden]{display:none}
.tjump{position:absolute;left:50%;bottom:14px;transform:translateX(-50%);border-radius:999px;min-height:40px;
padding:0 16px;font:700 12px var(--font-mono);background:var(--paper);color:var(--ink);border-color:var(--rule-soft);
box-shadow:0 4px 14px rgba(0,0,0,.35)}
.tjump[hidden]{display:none}
.tkeys{flex:none;display:flex;gap:6px;overflow-x:auto;-webkit-overflow-scrolling:touch;scrollbar-width:none;padding:1px}
.tkeys::-webkit-scrollbar{display:none}
.tkeys button{flex:none;min-width:48px;min-height:44px;padding:0 10px;font:700 13px var(--font-mono);
background:var(--card);color:var(--ink);border:1.5px solid var(--line);border-radius:8px}
.tkeys button[aria-pressed=true]{background:var(--ink);border-color:var(--ink);color:var(--paper)}
.tkeys button:disabled{opacity:.4;cursor:not-allowed}
.tkeys__gap{flex:1 0 6px}
.tpaste{flex:none;display:flex;gap:6px;align-items:flex-end;margin:0}
.tpaste textarea{flex:1;min-width:0;min-height:44px;max-height:22vh;margin:0;resize:none;font:16px/1.35 var(--font-mono)}
.tpaste textarea:disabled{opacity:.6}
.tpaste button{flex:none;min-height:44px;padding:0 12px;font:700 12px var(--font-mono)}
.tpaste button:disabled{opacity:.4;cursor:not-allowed}
.tnote{flex:none;min-height:1.3em;margin:0;font:600 12px/1.4 var(--font-mono);color:var(--accent)}
.tnote:empty{min-height:0}
@media (prefers-color-scheme:dark){.tframe,.tframe__msg{background:#0f0d09}.tframe{border-color:#3a352a}}
@media (max-width:760px){.term__head p{display:none}}
@media (max-width:480px){.tbar{padding:6px}.tbar__who{flex-basis:100%;order:3}.tbar__btns{margin-left:auto}
.tkeys button{min-width:44px;padding:0 8px}.tpaste button{padding:0 9px}}
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
font(n);label();toBottom();}
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


def render_terminal(ctx: Ctx, enabled: bool) -> str:
    """The Terminal page: a "confirm it is you" screen, and the live terminal it opens into.

    Nothing from firstmate's session is in this page: the screen only ever arrives over the
    websocket, after a finished step-up.
    """
    head = (
        "<div class=term__head><h1>Terminal</h1>"
        "<p class=mute>Firstmate’s own session, live, as it shows on the PC.</p></div>"
    )
    if not enabled:
        return _page(
            "Terminal · Hive desk",
            f"<div class=term id=term>{head}<div class=banner role=status>The Terminal page is "
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
        "<button class=danger type=submit>Open terminal</button>",
        always=True,
    ).replace("<form ", "<form id=term-open ", 1)
    body = (
        f"<div class=term id=term>{head}"
        "<div class=tbar id=term-bar data-s=closed role=status aria-live=polite>"
        "<span class='tstate tstate--closed' id=term-state>Not open</span>"
        "<span class=tbar__who id=term-who>Confirm it is you to open firstmate’s session."
        "</span><span class=tbar__btns>"
        "<button type=button class=danger id=term-take hidden>Take keyboard</button>"
        "<button type=button class=quiet id=term-release hidden>Release keyboard</button>"
        "</span></div><p class=act-warn id=term-warn role=alert hidden></p>"
        "<section class=tgate id=term-gate aria-label='Confirm it is you'><div class=card>"
        "<h2>Confirm it is you</h2>"
        "<p>The terminal shows firstmate’s own session, exactly as it is on the PC.</p>"
        "<ul><li>It opens read-only. Typing needs a second confirm: <b>Take keyboard</b>.</li>"
        "<li>Only one tab can hold the keyboard. It goes back to read-only after 10 minutes "
        "without typing.</li>"
        "<li>It closes after 30 minutes. Then confirm again.</li>"
        "<li>Nothing it shows or you type is saved or logged.</li></ul>"
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
        "<button type=button id=term-send disabled>Send</button>"
        "<button type=button class=danger id=term-send-enter disabled>Send + Enter</button></div>"
        "<p class=tnote id=term-note role=status aria-live=polite></p></section></div>"
    )
    tail = (
        f"<script src='/vendor/{XTERM_JS}' integrity='{XTERM_SRI}'></script>"
        f"<script>{TERMINAL_SCRIPT}</script>"
    )
    return _page(
        "Terminal · Hive desk", body, "terminal", "", ctx, css=XTERM_CSS + TERMINAL_CSS, tail=tail
    )
