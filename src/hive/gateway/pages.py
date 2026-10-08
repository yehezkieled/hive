# ruff: noqa: E501  (inline CSS)
"""HTML for the desk. Plain escaped strings plus one fixed inline script (``SCRIPT``,
pinned by hash in the CSP) for local times and self-refresh. Forms post to ``/act/*``."""

from __future__ import annotations

import base64
import hashlib
import re
from datetime import UTC, datetime, timedelta
from html import escape as esc
from urllib.parse import quote, urlencode, urlsplit
from zoneinfo import ZoneInfo

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

CSS = """
@font-face{font-family:"IBM Plex Mono";font-weight:400;font-display:swap;src:url(/fonts/ibm-plex-mono-400.woff2) format("woff2")}
@font-face{font-family:"IBM Plex Mono";font-weight:500;font-display:swap;src:url(/fonts/ibm-plex-mono-500.woff2) format("woff2")}
@font-face{font-family:"IBM Plex Mono";font-weight:600;font-display:swap;src:url(/fonts/ibm-plex-mono-600.woff2) format("woff2")}
@font-face{font-family:"IBM Plex Mono";font-weight:700;font-display:swap;src:url(/fonts/ibm-plex-mono-700.woff2) format("woff2")}
@font-face{font-family:"Nunito";font-weight:200 1000;font-display:swap;src:url(/fonts/nunito.woff2) format("woff2")}
@font-face{font-family:"Nunito Sans";font-weight:200 1000;font-display:swap;src:url(/fonts/nunito-sans.woff2) format("woff2")}
:root{
--paper:#faf7ed;--paper-2:#f2ecd8;--paper-soft:#f6f1de;--paper-shadow:rgba(60,45,25,.08);
--ink:#1f1812;--ink-2:#3d332a;--ink-3:#6f6356;--ink-4:#a79a89;
--rule:#1f1812;--rule-soft:#d9cfb6;--rule-faint:#e8dfc8;
--accent:#c8382a;--accent-soft:#f6dcd6;--amber:#b7741a;--amber-soft:#f4e4c8;
--honey:#e0a726;--honey-soft:#f9ecc6;--ochre:#8a6a1a;--sage:#4d8a3a;--sage-soft:#d7e6c5;
--bar:#1f1812;--bar-ink:#faf7ed;
--font-mono:"IBM Plex Mono",ui-monospace,Menlo,monospace;
--font-sans:"Nunito Sans","Inter",system-ui,sans-serif;
--font-display:"Nunito","Inter",system-ui,sans-serif;
--bg:var(--paper-2);--card:var(--paper);--mute:var(--ink-3);--line:var(--rule-soft);--acc:var(--amber);--ok:var(--sage)}
@media (prefers-color-scheme:dark){:root{
--paper:#201d16;--paper-2:#16140f;--paper-soft:#2a261d;--paper-shadow:rgba(0,0,0,.35);
--ink:#efe9da;--ink-2:#d6cfbf;--ink-3:#a39c8a;--ink-4:#7c7566;
--rule:#8f8775;--rule-soft:#3a352a;--rule-faint:#2e2a21;
--accent:#f0715f;--accent-soft:#4a2620;--amber:#e0a050;--amber-soft:#43331c;
--honey:#e8b640;--honey-soft:#3d3218;--ochre:#d9b45a;--sage:#7fc48c;--sage-soft:#22351f;
--bar:#efe9da;--bar-ink:#16140f}}
*,*::before,*::after{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 var(--font-sans);
-webkit-font-smoothing:antialiased;padding:env(safe-area-inset-top) 16px env(safe-area-inset-bottom)}
button,input,label,summary,a{touch-action:manipulation;-webkit-tap-highlight-color:transparent}
main{max-width:960px;margin:0 auto;padding:12px 0 calc(48px + var(--bar-room,0px) + env(safe-area-inset-bottom))}
h1{font:900 1.4rem var(--font-display);letter-spacing:-.4px;margin:.6rem 0}
h2{font:800 1.05rem var(--font-display);margin:1.6rem 0 .6rem}
a{color:var(--acc)}.mute{color:var(--mute);font-size:.88rem}
.chrome{max-width:960px;margin:0 auto;display:flex;align-items:center;gap:10px;padding:12px 2px 0}
.chrome>*{min-width:0}
.chrome__brand{font-family:var(--font-display);font-weight:900;font-size:17px;letter-spacing:-.3px;
color:var(--ink);text-decoration:none;display:inline-flex;align-items:center;min-height:44px}
.chrome__brand span{color:var(--accent)}
.chrome nav{margin-left:auto;display:flex;align-items:center;gap:2px;flex-wrap:wrap;justify-content:flex-end}
.chrome nav a,#alerts{display:inline-flex;align-items:center;min-height:44px;padding:0 10px;border-radius:999px;
font:700 11px var(--font-mono);letter-spacing:.4px;color:var(--ink-3);text-decoration:none;
background:transparent;border:1.5px solid transparent;cursor:pointer}
#alerts[hidden]{display:none}
.chrome nav a[aria-current]{color:var(--ink);border-color:var(--rule-soft);background:var(--paper)}
.tail{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px;
font:12px/1.35 var(--font-mono);white-space:pre-wrap;overflow-wrap:anywhere;
min-height:12rem;max-height:70vh;overflow:auto}
.banner{border:1px solid var(--accent);border-radius:10px;padding:12px;margin:12px 0;
background:var(--card)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
.need{background:var(--card);border:1px solid var(--accent);border-radius:12px;padding:12px;
margin:8px 0;overflow-wrap:anywhere}
.need .what{font-weight:700}.sub{display:block;color:var(--mute);font-size:.8rem;margin-top:2px}
.tag{display:inline-block;font:700 .66rem var(--font-mono);text-transform:uppercase;letter-spacing:.06em;
border:1px solid var(--line);border-radius:999px;padding:1px 8px;margin-right:6px;color:var(--mute)}
.row{padding:10px 0;border-top:1px solid var(--line);overflow-wrap:anywhere}
form{margin:8px 0}textarea,input[type=text],select{width:100%;font:inherit;color:var(--ink);
background:var(--paper);border:1px solid var(--line);border-radius:8px;padding:8px}
textarea{min-height:5.5rem}label{display:block;font-size:.88rem;color:var(--mute);margin:6px 0 2px}
button{font:inherit;border:1.5px solid var(--ink);background:var(--ink);color:var(--paper);
border-radius:8px;padding:8px 14px;min-height:44px;cursor:pointer}
button.quiet{background:transparent;color:var(--ink)}button.danger{background:var(--accent);border-color:var(--accent);color:#fff}
.inline{display:inline-block;margin:4px 6px 4px 0}details{margin-top:8px}summary{cursor:pointer;color:var(--acc);
min-height:32px}
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
.chatpage .chrome{flex:none;width:100%;padding-left:12px;padding-right:12px;box-sizing:border-box}
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
@media (max-width:480px){.msg{max-width:94%}.chrome nav a,#alerts{padding:0 7px}.dbar__go{padding:0 12px}}
/* quota chip: the busier of the two plan windows, as percent used; tap shows both */
.qwrap{position:relative;margin:0}
.qchip{display:inline-flex;align-items:center;gap:7px;border:1.5px solid var(--rule-soft);border-radius:999px;
background:var(--paper);padding:0 12px;min-height:44px;font:700 11px var(--font-mono);cursor:pointer;
color:var(--ink);list-style:none;white-space:nowrap}
.qchip::-webkit-details-marker{display:none}
.qchip__bar{width:30px;height:5px;border-radius:3px;background:var(--rule-faint);overflow:hidden;display:inline-block}
.qchip__bar i{display:block;height:100%;background:var(--sage)}
.qchip--warn{border-color:var(--amber)}.qchip--warn .qchip__bar i{background:var(--amber)}
.qchip--hot{border-color:var(--accent);background:var(--accent-soft)}.qchip--hot .qchip__bar i{background:var(--accent)}
.qchip--unknown{color:var(--ink-4)}
.qpop{position:absolute;right:0;top:50px;z-index:5;background:var(--paper);border:1.5px solid var(--rule);
border-radius:12px;padding:10px 12px;box-shadow:0 8px 24px var(--paper-shadow);font:10.5px/1.4 var(--font-mono);
min-width:210px;color:var(--ink-2)}
.qpop{max-width:min(320px,calc(100vw - 24px))}
.qpop__alerts{margin-top:6px;padding-top:8px;border-top:1px solid var(--rule-faint);white-space:normal}
.qpop__alerts h3{margin:0 0 4px;font:800 11px var(--font-mono);color:var(--ink);text-transform:uppercase;letter-spacing:.04em}
.qpop__alerts p{margin:0 0 4px}.qpop__alerts ol{margin:0 0 4px;padding-left:18px}.qpop__alerts li{padding:2px 0}
.qpop__alerts #alerts-note:empty{display:none}
.qpop div{display:flex;justify-content:space-between;gap:14px;padding:3px 0}
.qpop b{color:var(--ink)}.qpop span,.qpop b{white-space:nowrap}.qpop .qpop__note{color:var(--ink-4)}
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
.wide main,.wide .chrome{max-width:1480px}
.wide .screen{min-height:calc(100vh - 96px);gap:16px}
.wide .land{grid-template-columns:minmax(440px,5fr) minmax(0,7fr);gap:20px}
.wide .land__col{gap:16px}
.wide .pcs{grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}
.wide .dbar-wrap{margin-top:auto;position:sticky;bottom:12px}
.wide .dbar{padding:10px 10px 10px 16px;box-shadow:0 8px 24px var(--paper-shadow)}
.wide input.dbar__in{font-size:15px}
}
/* one-page desk on PC, laptop and tablet: the viewport is the page; each panel scrolls on its own */
@media (min-width:700px) and (min-height:560px){
body.wide{height:100vh;height:100dvh;overflow:hidden;display:flex;flex-direction:column}
.wide .chrome{flex:none;width:100%}
.wide main{flex:1;min-height:0;width:100%;display:flex;flex-direction:column;padding:8px 0 calc(12px + env(safe-area-inset-bottom))}
.wide .screen{flex:1;min-height:0;gap:10px}
.wide .land{flex:1;min-height:0;grid-template-columns:minmax(0,1fr) minmax(0,1fr);grid-template-rows:minmax(0,1fr);gap:14px;align-items:stretch}
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
.wide .dbar-wrap{position:static;margin:0;flex:none}
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
.btn{font:600 11px var(--font-mono);letter-spacing:.4px;padding:0 14px;min-height:44px;display:inline-flex;align-items:center;
border:1.5px solid var(--rule);border-radius:7px;background:var(--paper);color:var(--ink);cursor:pointer;
white-space:nowrap;text-decoration:none}
.btn:active{transform:translateY(1px)}
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
if(document.querySelector('details[open],dialog[open],button.is-armed,form.is-sending'))return true;
var x=document.querySelectorAll('textarea,input:not([type]),input[type=text],input[type=search]');
for(var i=0;i<x.length;i++)if(x[i].value)return true;
x=document.querySelectorAll('input[type=checkbox]');
for(var j=0;j<x.length;j++)if(x[j].checked!==x[j].defaultChecked)return true;
return false;}
function load(cb){fetch(location.href,{credentials:'same-origin',cache:'no-store'})
.then(function(r){return r.ok?r.text():null;}).then(function(t){
if(t)cb(new DOMParser().parseFromString(t,'text/html'));}).catch(function(){});}
var b=document.body,every=+b.getAttribute('data-refresh'),poll=+b.getAttribute('data-poll');
var tail=b.getAttribute('data-tail');
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
return d.length===1&&d[0].id==='rvs-sheet'&&!document.querySelector('details[open],.rv__x.is-armed')&&!sel();}
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
var q=doc.getElementById('qchip'),oq=document.getElementById('qchip');
if(q){var qt=q.getElementsByTagName('time');for(var i=0;i<qt.length;i++)stamp(qt[i]);}
if(q&&oq&&!oq.open&&q.outerHTML!==oq.outerHTML)oq.outerHTML=q.outerHTML;
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
es.addEventListener('chat',function(){seen=Date.now();if(th)refreshThread();});}
if(poll&&th)setInterval(function(){if(!live)refreshThread();},poll);
else if(every&&c)setInterval(function(){if(!live)refreshMain();},every);
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
extra.step=step;extra.rid=fm.getAttribute('data-rid');
rvPost(fm,extra,function(j){rvDisarm(fm);
if(j.ok){var row=fm.closest('.rvrow');if(row&&!fm.classList.contains('rv__x--bulk')&&row.parentNode)row.parentNode.removeChild(row);refreshMain();}
else{b.textContent='Failed';b.title=j.message||'';fm.rvT=setTimeout(function(){rvDisarm(fm);},3000);}});});
var CONFIRMED={merge:1,control:1};
function actName(fm){return (fm.getAttribute('action')||'').replace('/act/','');}
function actDisarm(fm,b){clearTimeout(fm.acT);fm.removeAttribute('data-step');fm.removeAttribute('data-extra');
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
if(step){clearTimeout(fm.acT);b.classList.remove('is-armed');p.set('step',step);var x={};try{x=JSON.parse(fm.getAttribute('data-extra')||'{}');}catch(_){}
for(var k in x){if(Object.prototype.hasOwnProperty.call(x,k))p.set(k,x[k]);}
p.set('rid',fm.getAttribute('data-rid')||p.get('rid'));}
var two=CONFIRMED[act]&&!step;
fm.classList.add('is-sending');
if(!two){b.textContent='Sent ✓';b.classList.add('is-sent');}
actSettle(fm,p,function(j){fm.classList.remove('is-sending');
if(j.ok&&j.confirm){b.classList.remove('is-sent');fm.setAttribute('data-step',j.step);fm.setAttribute('data-rid',j.rid);
fm.setAttribute('data-extra',JSON.stringify(j.extra||{}));fm.acBtn=sig;fm.acEl=b;b.classList.add('is-armed');b.textContent=j.label||'Tap again';
fm.acT=setTimeout(function(){actDisarm(fm,b);},8000);return;}
if(j.ok){fm.classList.add('is-sent');b.textContent=j.pending?'Sent ✓ saving…':'Sent ✓';
b.classList.remove('is-armed');b.classList.add('is-sent');
var t=fm.querySelector('textarea,input[type=text]');if(t)t.value='';
if(act==='ticket')setTimeout(function(){fm.classList.remove('is-sent');b.classList.remove('is-sent');b.textContent=b.getAttribute('data-was');freshRid(fm);},2500);return;}
b.classList.remove('is-sent','is-armed');b.textContent='Failed ✗';b.title=j.message||'';
var m=fm.querySelector('.act-err');if(!m){m=document.createElement('p');m.className='act-err';m.setAttribute('role','alert');fm.appendChild(m);}
m.textContent=j.message||'Could not send.';
fm.removeAttribute('data-step');fm.removeAttribute('data-extra');if(!j.net)freshRid(fm);
setTimeout(function(){b.textContent=b.getAttribute('data-was');},3000);},0);});
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
if(hit)pick(g[i],hit,false);else delete picks[k];}sync(g[i]);}describeAll();barRoom();}
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
if(th&&window.visualViewport){var vv=window.visualViewport;
function fit(){var was=atBottom();document.documentElement.style.setProperty('--app-h',vv.height+'px');
var d=document.querySelector('.dock');if(d)document.documentElement.style.setProperty('--dock-h',d.offsetHeight+'px');
window.scrollTo(0,0);if(was)bottom();}
vv.addEventListener('resize',fit);vv.addEventListener('scroll',function(){window.scrollTo(0,0);});fit();}
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
    ) -> None:
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

    def form(self, action: str, inner: str, cls: str = "", **hidden: str) -> str:
        if not self.writable:
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
            f"<span class=qchip__bar><i style='width:{worst.used}%'></i></span>{worst.used}% used</summary>"
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
        f"<details class=qwrap id=qchip>{chip}<div class=qpop>{rows}"
        "<div class=qpop__note><span>account-wide · shared with your own use</span></div>"
        "<section class=qpop__alerts aria-labelledby=qpop-alerts-h>"
        "<h3 id=qpop-alerts-h>Get alerts</h3>"
        "<p>On iPhone or iPad (iOS 16.4 and up):</p>"
        "<ol><li>Tap Share in Safari.</li><li>Tap Add to Home Screen.</li>"
        "<li>Open the desk from its Home Screen icon, then tap Alerts.</li></ol>"
        "<p id=alerts-note role=status></p></section>"
        "</div></details>"
    )


def _page(title: str, body: str, active: str = "", attrs: str = "", ctx: Ctx | None = None) -> str:
    def cur(name: str) -> str:
        return " aria-current=page" if active == name else ""

    chip = _chip(ctx) if ctx is not None else ""
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1,viewport-fit=cover'>"
        "<meta name=color-scheme content='light dark'>"
        "<meta name=theme-color content='#b4531a'>"
        "<meta name=apple-mobile-web-app-capable content=yes>"
        "<meta name=apple-mobile-web-app-title content='Hive desk'>"
        "<link rel=manifest href='/manifest.webmanifest'>"
        "<link rel=icon href='/icons/icon-192.png'>"
        "<link rel=apple-touch-icon href='/icons/apple-touch-icon-180.png'>"
        f"<title>{esc(title)}</title><style>{CSS}</style></head>"
        f"<body{attrs}{' class=wide' if active == 'desk' else ' class=chatpage' if active == 'chat' else ''}><header class=chrome>"
        "<a class=chrome__brand href='/' aria-label='Hive desk'>hive<span>.</span></a>"
        f"<nav><a href='/'{cur('desk')}>Desk</a><a href='/chat'{cur('chat')}>Chat</a>"
        f"<button id=alerts hidden type=button>Alerts</button></nav>{chip}"
        f"</header><main>{body}</main><script>{SCRIPT}</script></body></html>"
    )


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
        )
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
        )
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
