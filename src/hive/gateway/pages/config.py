# ruff: noqa: E501  (inline HTML)
"""The Config page: firstmate's merge posture and worker profiles, read-only."""

from __future__ import annotations

from html import escape as esc

from hive.gateway import fmconfig
from hive.gateway.pages.shell import Ctx, _page, _sc, _tabs


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
