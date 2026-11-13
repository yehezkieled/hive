"""HTML for the desk. Plain escaped strings plus one fixed inline script (SCRIPT,
pinned by hash in the CSP) for local times and self-refresh. Forms post to /act/*.

One module per page over a shared shell: style and script hold the inlined CSS and JS,
shell the per-render context, the top bar and the page frame, needs the decision cards;
then home, project, chat, config, machine, run, signins, terminal
and results. Callers use this package's names (pages.render_home, pages.Ctx), which
are re-exported here.
"""

from __future__ import annotations

from hive.gateway.pages.chat import render_chat
from hive.gateway.pages.config import render_config
from hive.gateway.pages.home import render_home
from hive.gateway.pages.machine import MACHINE_REFRESH_MS, render_machine
from hive.gateway.pages.project import render_project, render_watch
from hive.gateway.pages.results import render_confirm, render_outcome, render_result
from hive.gateway.pages.run import RUN_REFRESH_MS, render_run
from hive.gateway.pages.script import SCRIPT, SCRIPT_CSP_HASH
from hive.gateway.pages.shell import BOARD_RE, COMB, MARK, NAV, Ctx, _chip
from hive.gateway.pages.signins import (
    SIGNINS_REFRESH_MS,
    render_signin_flow,
    render_signins,
    signin_flow,
)
from hive.gateway.pages.style import CSS
from hive.gateway.pages.terminal import (
    TERMINAL_CSS,
    TERMINAL_SCRIPT,
    TERMINAL_SCRIPT_CSP_HASH,
    VENDOR_DIR,
    XTERM_CSS,
    XTERM_JS,
    XTERM_SRI,
    render_terminal,
)

__all__ = [
    "BOARD_RE",
    "COMB",
    "CSS",
    "Ctx",
    "MACHINE_REFRESH_MS",
    "MARK",
    "NAV",
    "RUN_REFRESH_MS",
    "SCRIPT",
    "SCRIPT_CSP_HASH",
    "SIGNINS_REFRESH_MS",
    "TERMINAL_CSS",
    "TERMINAL_SCRIPT",
    "TERMINAL_SCRIPT_CSP_HASH",
    "VENDOR_DIR",
    "XTERM_CSS",
    "XTERM_JS",
    "XTERM_SRI",
    "_chip",
    "render_chat",
    "render_config",
    "render_confirm",
    "render_home",
    "render_machine",
    "render_outcome",
    "render_project",
    "render_result",
    "render_run",
    "render_signin_flow",
    "render_signins",
    "render_terminal",
    "render_watch",
    "signin_flow",
]
