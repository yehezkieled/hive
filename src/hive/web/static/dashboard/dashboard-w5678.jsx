(function () {
// Widgets 5–8: Bubble matrix, Cache hit bars, Audit log, Failure scatter

const { D: D58, dStyles: dS58, D_Card: DC58, D_NSPill, D_PriorityPill: D_PP58, D_StateDot: D_SD58 } = window;

// ═══ WIDGET 5: COST-BY-ENTITY BUBBLE MATRIX ════════════════

function W5_BubbleMatrix() {
  const m = window.HIVE_DASH.matrix;
  const [hover, setHover] = React.useState(null);

  // total per entity
  const totals = m.entities.map(e => ({
    name: e,
    total: m.models.reduce((s, mod) => s + (m.cells[e][mod] || 0), 0),
  }));
  const grand = totals.reduce((s, t) => s + t.total, 0);
  const maxCell = Math.max(...m.entities.flatMap(e => m.models.map(mod => m.cells[e][mod] || 0)));

  const ROW = 40, COL = 64, LABEL_W = 70;

  return (
    <DC58 title="Cost by entity × model" sub="bubble size = $ cost · color = % of that entity's spend" style={{ width: '100%', height: '100%' }}>
      <div style={{ padding: '12px 12px' }}>
        <div style={{ minWidth: 0 }}>
          {/* model header row */}
          <div style={{ display: 'grid', gridTemplateColumns: `${LABEL_W}px repeat(${m.models.length}, minmax(0, 1fr)) 60px`, alignItems: 'center', marginBottom: 8 }}>
            <div></div>
            {m.models.map(mod => (
              <div key={mod} style={{
                fontFamily: dS58.mono, fontSize: 11, fontWeight: 700, color: D58.ink2,
                textAlign: 'center', padding: '4px 0',
                borderBottom: `1px dashed ${D58.ruleSoft}`,
              }}>{mod}</div>
            ))}
            <div style={{
              fontFamily: dS58.mono, fontSize: 11, fontWeight: 700, color: D58.ink3,
              textAlign: 'right', padding: '4px 6px', borderBottom: `1px dashed ${D58.ruleSoft}`,
            }}>total</div>
          </div>

          {m.entities.map(e => {
            const eTotal = totals.find(t => t.name === e).total;
            return (
              <div key={e} className="d-row" style={{
                display: 'grid', gridTemplateColumns: `${LABEL_W}px repeat(${m.models.length}, minmax(0, 1fr)) 60px`,
                alignItems: 'center', height: ROW, borderBottom: `1px dashed ${D58.ruleFaint}`,
              }}>
                <div style={{ fontFamily: dS58.mono, fontSize: 12, fontWeight: 700, color: D58.ink, paddingLeft: 4 }}>
                  /m:{e}
                </div>
                {m.models.map(mod => {
                  const v = m.cells[e][mod] || 0;
                  const r = v > 0 ? Math.max(4, Math.sqrt(v / maxCell) * 22) : 0;
                  const pct = eTotal > 0 ? (v / eTotal) : 0;
                  // color by % of entity total: small=ink3, medium=honey, large=accent
                  const color = pct > 0.5 ? D58.accent : pct > 0.25 ? D58.honey : pct > 0.05 ? D58.ink3 : D58.ink5;
                  const isHover = hover && hover.e === e && hover.mod === mod;
                  return (
                    <div key={mod} style={{
                      display: 'grid', placeItems: 'center', position: 'relative',
                    }}>
                      {v > 0 ? (
                        <div className="d-bubble"
                          onMouseEnter={() => setHover({ e, mod, v, pct })}
                          onMouseLeave={() => setHover(null)}
                          style={{
                            width: r * 2, height: r * 2, borderRadius: '50%',
                            background: color, opacity: 0.9, cursor: 'pointer',
                            border: isHover ? `2px solid ${D58.ink}` : `1px solid ${D58.ink}22`,
                            boxShadow: isHover ? `0 0 0 4px ${D58.honeySoft}` : 'none',
                          }}
                        />
                      ) : (
                        <span style={{ color: D58.ink5, fontSize: 14, fontFamily: dS58.mono }}>·</span>
                      )}
                    </div>
                  );
                })}
                <div style={{
                  fontFamily: dS58.mono, fontSize: 12, fontWeight: 700, color: eTotal > 0 ? D58.ink : D58.ink5,
                  textAlign: 'right', paddingRight: 6,
                }}>
                  {eTotal > 0 ? `$${eTotal.toFixed(2)}` : '—'}
                </div>
              </div>
            );
          })}

          {/* footer row */}
          <div style={{ display: 'grid', gridTemplateColumns: `${LABEL_W}px repeat(${m.models.length}, minmax(0, 1fr)) 60px`, alignItems: 'center', marginTop: 6 }}>
            <div style={{ fontFamily: dS58.mono, fontSize: 10, fontWeight: 700, color: D58.ink3, paddingLeft: 4 }}>total</div>
            {m.models.map(mod => {
              const t = m.entities.reduce((s, e) => s + (m.cells[e][mod] || 0), 0);
              return (
                <div key={mod} style={{ textAlign: 'center', fontFamily: dS58.mono, fontSize: 11, fontWeight: 700, color: D58.ink2 }}>
                  ${t.toFixed(2)}
                </div>
              );
            })}
            <div style={{
              fontFamily: dS58.display, fontSize: 14, fontWeight: 800, color: D58.ink,
              textAlign: 'right', paddingRight: 6,
            }}>${grand.toFixed(2)}</div>
          </div>
        </div>

        {/* model mix bar — fills the bottom of the card */}
        {(() => {
          const modelTotals = m.models.map(mod => ({
            mod,
            total: m.entities.reduce((s, e) => s + (m.cells[e][mod] || 0), 0),
          })).sort((a, b) => b.total - a.total);
          const palette = {
            haiku:    D58.sage,
            sonnet:   D58.honey,
            opus:     D58.amber,
            opusplan: D58.accent,
          };
          return (
            <div style={{ marginTop: 16, paddingTop: 12, borderTop: `1px dashed ${D58.ruleSoft}` }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 8 }}>
                <span style={{ fontFamily: dS58.mono, fontSize: 10.5, fontWeight: 800, color: D58.ink2, letterSpacing: '0.04em', textTransform: 'uppercase' }}>
                  model mix
                </span>
                <span style={{ fontFamily: dS58.mono, fontSize: 10, color: D58.ink3, fontWeight: 600 }}>
                  share of $ spend · last 7d
                </span>
                <span style={{ marginLeft: 'auto', fontFamily: dS58.mono, fontSize: 10, color: D58.ink3, fontWeight: 600 }}>
                  {(() => {
                    if (!modelTotals.length || grand <= 0) return null;
                    const top = modelTotals.reduce((a, b) => (b.total > a.total ? b : a));
                    const entityCount = m.entities.filter(e => (m.cells[e]?.[top.mod] || 0) > 0).length;
                    return `${top.mod} = ${((top.total / grand) * 100).toFixed(0)}% of spend, ${entityCount} ${entityCount === 1 ? 'entity' : 'entities'}`;
                  })()}
                </span>
              </div>
              <div style={{ display: 'flex', height: 22, borderRadius: 4, overflow: 'hidden', border: `1px solid ${D58.ruleSoft}` }}>
                {modelTotals.map(({ mod, total }) => {
                  const pct = (total / grand) * 100;
                  if (pct < 0.5) return null;
                  return (
                    <div key={mod} style={{
                      width: `${pct}%`, background: palette[mod], opacity: 0.78,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      fontFamily: dS58.mono, fontSize: 10, fontWeight: 800,
                      color: mod === 'opusplan' || mod === 'opus' ? '#fff' : D58.ink,
                      textShadow: (mod === 'opusplan' || mod === 'opus') ? '0 1px 1px rgba(0,0,0,0.25)' : 'none',
                      borderRight: `1px solid ${D58.paper}66`,
                    }}>
                      {pct >= 8 ? `${mod} ${pct.toFixed(0)}%` : `${pct.toFixed(0)}%`}
                    </div>
                  );
                })}
              </div>
              <div style={{
                marginTop: 8, display: 'flex', justifyContent: 'space-between',
                fontFamily: dS58.mono, fontSize: 9.5, color: D58.ink3, fontWeight: 600,
              }}>
                <span>{m.entities.length} entities · {m.models.length} models · {m.entities.reduce((s, e) => s + m.models.filter(mod => m.cells[e][mod] > 0).length, 0)} active cells</span>
                <span>{(() => {
                  let best = null;
                  for (const e of m.entities) {
                    for (const mod of m.models) {
                      const v = m.cells[e]?.[mod] || 0;
                      if (!best || v > best.v) best = { e, mod, v };
                    }
                  }
                  return best && best.v > 0
                    ? `biggest line: /m:${best.e} × ${best.mod} · $${best.v.toFixed(2)}`
                    : 'biggest line: —';
                })()}</span>
              </div>
            </div>
          );
        })()}
      </div>

      {hover && (
        <div style={{
          position: 'absolute', right: 16, bottom: 12,
          background: D58.ink, color: D58.paper, fontFamily: dS58.mono,
          fontSize: 10, padding: '7px 10px', borderRadius: 6, lineHeight: 1.55,
        }}>
          <div style={{ color: D58.honey, fontWeight: 700 }}>/m:{hover.e} × {hover.mod}</div>
          <div>cost: <b>${hover.v.toFixed(2)}</b></div>
          <div>{(hover.pct * 100).toFixed(0)}% of entity spend</div>
          <div style={{ color: D58.ink5 }}>click to filter the page →</div>
        </div>
      )}
    </DC58>
  );
}

// ═══ WIDGET 6: CACHE HIT RATE ══════════════════════════════

function W6_CacheHit() {
  const rows = [...window.HIVE_DASH.cacheRows].sort((a, b) => b.hit - a.hit);
  const overall = window.HIVE_DASH.cacheOverall;
  const [hover, setHover] = React.useState(null);

  const sparkW = 80, sparkH = 22;
  const min = Math.min(...overall.sparkline), max = Math.max(...overall.sparkline);
  const sx = i => (i / (overall.sparkline.length - 1)) * sparkW;
  const sy = v => sparkH - ((v - min) / (max - min || 1)) * sparkH;

  // savings math: cache-read priced ~10× cheaper than fresh input.
  // approximate: fresh-equivalent cost = (cached + fresh) × $3/1M; actual = cached × $0.30/1M + fresh × $3/1M
  const totals = window.HIVE_DASH.cacheRows.reduce((a, r) => ({
    cached: a.cached + r.tokens.cached,
    fresh:  a.fresh  + r.tokens.fresh,
  }), { cached: 0, fresh: 0 });
  const totalTok = totals.cached + totals.fresh;
  const cachedPct = (totals.cached / totalTok) * 100;
  const fullCost  = (totalTok * 3) / 1e6;
  const realCost  = (totals.cached * 0.3 + totals.fresh * 3) / 1e6;
  const saved     = fullCost - realCost;
  const savedPct  = (saved / fullCost) * 100;

  return (
    <DC58 title="Cache hit rate" sub="per-entity · sorted by hit % desc" style={{ width: '100%', height: '100%' }}>
      <div style={{
        display: 'flex', gap: 16, alignItems: 'center', padding: '14px 16px 10px',
        borderBottom: `1px dashed ${D58.ruleSoft}`,
      }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
            <span style={{ fontFamily: dS58.display, fontSize: 32, fontWeight: 800, color: D58.ink, letterSpacing: -1, lineHeight: 1 }}>
              {overall.hit}%
            </span>
            <span style={{ fontSize: 11, color: D58.ink3, fontWeight: 700 }}>overall</span>
          </div>
          <div style={{ fontSize: 11, color: D58.ink3, fontFamily: dS58.mono, fontWeight: 600, marginTop: 2 }}>
            7-day rolling
          </div>
        </div>
        <svg viewBox={`0 0 ${sparkW} ${sparkH}`} width={sparkW} height={sparkH}>
          <path
            d={`M ${overall.sparkline.map((v, i) => `${sx(i)} ${sy(v)}`).join(' L ')}`}
            stroke={D58.vault} strokeWidth="1.5" fill="none" />
          <circle cx={sx(overall.sparkline.length - 1)} cy={sy(overall.sparkline[overall.sparkline.length - 1])}
            r="2.5" fill={D58.vault} />
        </svg>
        <div style={{ marginLeft: 'auto', textAlign: 'right' }}>
          <div style={{ fontFamily: dS58.mono, fontSize: 10, color: D58.accent, fontWeight: 700 }}>
            ⚠ 1 anomaly
          </div>
          <div style={{ fontFamily: dS58.mono, fontSize: 10, color: D58.ink3, fontWeight: 600 }}>
            research dropped 29pt
          </div>
        </div>
      </div>

      <div style={{ padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: 9 }}>
        {rows.map(r => {
          const drop = r.baseline - r.hit;
          const isAnom = r.anomaly;
          return (
            <div key={r.name}
              onMouseEnter={() => setHover(r)}
              onMouseLeave={() => setHover(null)}
              style={{
                display: 'grid', gridTemplateColumns: '78px 1fr 60px',
                alignItems: 'center', gap: 12,
                padding: '6px 8px', borderRadius: 8,
                border: isAnom ? `1.5px solid ${D58.accent}` : `1px solid transparent`,
                background: isAnom ? D58.accentSoft : 'transparent',
                cursor: 'pointer',
                boxShadow: isAnom ? `0 0 12px ${D58.accent}22` : 'none',
              }}
            >
              <span style={{ fontFamily: dS58.mono, fontSize: 12, fontWeight: 700, color: D58.ink }}>
                /m:{r.name}
              </span>
              <div style={{ position: 'relative', height: 18, background: D58.paperSoft, borderRadius: 4, border: `1px solid ${D58.ruleSoft}`, overflow: 'hidden' }}>
                <div style={{
                  position: 'absolute', left: 0, top: 0, bottom: 0,
                  width: `${r.hit}%`,
                  background: isAnom ? D58.accent : D58.vault,
                  opacity: isAnom ? 0.85 : 0.78,
                }} />
                {/* baseline tick */}
                <div style={{
                  position: 'absolute', top: -2, bottom: -2, left: `${r.baseline}%`, width: 1.5,
                  background: D58.ink, opacity: 0.8,
                }} title={`baseline ${r.baseline}%`} />
                <span style={{
                  position: 'absolute', right: 6, top: 1, fontFamily: dS58.mono, fontSize: 10,
                  fontWeight: 800, color: '#fff',
                  textShadow: '0 1px 1px rgba(0,0,0,0.3)',
                }}>{r.hit}%</span>
              </div>
              <span style={{
                fontFamily: dS58.mono, fontSize: 10, fontWeight: 700,
                color: drop > 10 ? D58.accent : drop > 0 ? D58.amber : D58.sage,
                textAlign: 'right',
              }}>
                {drop > 0 ? `−${drop}pt` : `+${Math.abs(drop)}pt`}
              </span>
            </div>
          );
        })}

        <div style={{ marginTop: 4, fontSize: 10.5, fontFamily: dS58.mono, color: D58.ink3, fontWeight: 600, display: 'flex', gap: 12, paddingTop: 6, borderTop: `1px dashed ${D58.ruleSoft}` }}>
          <span><span style={{ display: 'inline-block', width: 10, height: 10, background: D58.vault, opacity: 0.78, marginRight: 4, verticalAlign: 'middle', borderRadius: 2 }} />cached</span>
          <span><span style={{ display: 'inline-block', width: 1.5, height: 10, background: D58.ink, marginRight: 4, verticalAlign: 'middle' }} />7-day baseline</span>
          <span style={{ marginLeft: 'auto' }}>red row = &gt;10pt drop vs own baseline</span>
        </div>
      </div>

      {/* ── savings panel ── */}
      <div style={{
        margin: '0 16px 14px', marginTop: 'auto', padding: '12px 14px',
        background: D58.paperSoft, border: `1px solid ${D58.ruleSoft}`, borderRadius: 8,
        display: 'grid', gridTemplateColumns: '1fr auto', gap: 14, alignItems: 'center',
      }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, marginBottom: 6 }}>
            <span style={{ fontFamily: dS58.mono, fontSize: 10, fontWeight: 800, color: D58.ink, letterSpacing: 0.5, textTransform: 'uppercase' }}>
              tokens served
            </span>
            <span style={{ fontFamily: dS58.mono, fontSize: 10, color: D58.ink3, fontWeight: 600 }}>
              {(totalTok / 1e6).toFixed(2)}M total · 7d
            </span>
          </div>
          <div style={{ position: 'relative', height: 14, background: D58.paper, border: `1px solid ${D58.ruleSoft}`, borderRadius: 3, overflow: 'hidden', display: 'flex' }}>
            <div style={{ width: `${cachedPct}%`, background: D58.vault, opacity: 0.82, display: 'flex', alignItems: 'center', paddingLeft: 6 }}>
              <span style={{ fontFamily: dS58.mono, fontSize: 9.5, fontWeight: 800, color: '#fff', textShadow: '0 1px 1px rgba(0,0,0,0.25)' }}>
                {cachedPct.toFixed(0)}% cached · {(totals.cached / 1e6).toFixed(2)}M
              </span>
            </div>
            <div style={{ flex: 1, background: D58.honey, opacity: 0.55, display: 'flex', alignItems: 'center', justifyContent: 'flex-end', paddingRight: 6 }}>
              <span style={{ fontFamily: dS58.mono, fontSize: 9.5, fontWeight: 800, color: D58.ink }}>
                {(totals.fresh / 1e6).toFixed(2)}M fresh
              </span>
            </div>
          </div>
        </div>

        <div style={{ textAlign: 'right', borderLeft: `1px dashed ${D58.ruleSoft}`, paddingLeft: 14 }}>
          <div style={{ fontFamily: dS58.mono, fontSize: 10, fontWeight: 800, color: D58.ink, letterSpacing: 0.5, textTransform: 'uppercase' }}>
            saved by cache
          </div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, justifyContent: 'flex-end', marginTop: 2 }}>
            <span style={{ fontFamily: dS58.display, fontSize: 22, fontWeight: 800, color: D58.sage, letterSpacing: -0.5, lineHeight: 1 }}>
              ${saved.toFixed(2)}
            </span>
            <span style={{ fontFamily: dS58.mono, fontSize: 11, fontWeight: 700, color: D58.sage }}>
              −{savedPct.toFixed(0)}%
            </span>
          </div>
          <div style={{ fontFamily: dS58.mono, fontSize: 10, color: D58.ink3, fontWeight: 600, marginTop: 2 }}>
            vs ${fullCost.toFixed(2)} uncached
          </div>
        </div>
      </div>

      {hover && (
        <div style={{
          position: 'absolute', right: 16, bottom: 12,
          background: D58.ink, color: D58.paper, fontFamily: dS58.mono,
          fontSize: 10, padding: '7px 10px', borderRadius: 6, lineHeight: 1.55,
        }}>
          <div style={{ color: D58.honey, fontWeight: 700 }}>/m:{hover.name}</div>
          <div>cached: {(hover.tokens.cached / 1e6).toFixed(2)}M tok</div>
          <div>fresh:  {(hover.tokens.fresh / 1e6).toFixed(2)}M tok</div>
          <div>delta vs 7d avg: {hover.hit - hover.baseline > 0 ? '+' : ''}{hover.hit - hover.baseline}pt</div>
        </div>
      )}
    </DC58>
  );
}

// ═══ WIDGET 7: AUDIT LOG ═══════════════════════════════════

function W7_AuditLog() {
  const hist = window.HIVE_DASH.histogram;
  const feed = window.HIVE_DASH.auditFeed;
  const [filters, setFilters] = React.useState(new Set(['command','entity','task','git']));
  const [hoverBucket, setHoverBucket] = React.useState(null);
  const [expanded, setExpanded] = React.useState(null);

  const nsColors = {
    command: D58.ink3, entity: D58.honey, task: D58.sage, git: D58.vault,
  };
  const nsLabel = {
    all: 'all', command: 'command', entity: 'entity', task: 'task', git: 'git',
  };

  function toggle(ns) {
    const next = new Set(filters);
    if (ns === 'all') {
      if (next.size === 4) next.clear();
      else { next.clear(); ['command','entity','task','git'].forEach(x => next.add(x)); }
    } else {
      if (next.has(ns)) next.delete(ns); else next.add(ns);
    }
    setFilters(next);
  }

  // histogram max
  const totals = hist.map(b => filters.size === 0 ? 0 :
    [...filters].reduce((s, ns) => s + b[ns], 0));
  const maxBar = Math.max(...totals, 1);

  const visibleFeed = feed.filter(e => filters.has(e.ns));

  return (
    <DC58 title="Audit log" sub="last 60 minutes · histogram + feed">
      {/* filter row */}
      <div style={{
        padding: '10px 16px', borderBottom: `1px dashed ${D58.ruleSoft}`,
        display: 'flex', alignItems: 'center', gap: 7, flexWrap: 'wrap',
      }}>
        <button className="d-chip" onClick={() => toggle('all')} style={{
          padding: '3px 10px', borderRadius: 999, border: `1px solid ${D58.ruleSoft}`,
          background: filters.size === 4 ? D58.ink : D58.paper,
          color: filters.size === 4 ? D58.paper : D58.ink2,
          fontFamily: dS58.mono, fontSize: 11, fontWeight: 700, cursor: 'pointer',
        }}>all</button>
        {['command','entity','task','git'].map(ns => (
          <button key={ns} className="d-chip" onClick={() => toggle(ns)} style={{
            padding: '3px 10px', borderRadius: 999,
            border: `1px solid ${filters.has(ns) ? nsColors[ns] : D58.ruleSoft}`,
            background: filters.has(ns) ? nsColors[ns] + '22' : D58.paper,
            color: filters.has(ns) ? D58.ink : D58.ink3,
            fontFamily: dS58.mono, fontSize: 11, fontWeight: 700, cursor: 'pointer',
            display: 'inline-flex', alignItems: 'center', gap: 5,
          }}>
            <span style={{ width: 7, height: 7, background: nsColors[ns], borderRadius: '50%' }} />
            {ns}
          </button>
        ))}
        <span style={{ marginLeft: 'auto', fontFamily: dS58.mono, fontSize: 11, color: D58.ink3, fontWeight: 600 }}>
          {visibleFeed.length} events visible
        </span>
      </div>

      {/* histogram */}
      <div style={{
        padding: '12px 16px 6px',
        background: D58.paperSoft,
        borderBottom: `1px solid ${D58.ruleSoft}`,
      }}>
        <div style={{
          height: 56, display: 'flex', gap: 1.5, alignItems: 'flex-end',
        }} onMouseLeave={() => setHoverBucket(null)}>
          {hist.map(b => {
            const tot = [...filters].reduce((s, ns) => s + b[ns], 0);
            const h = (tot / maxBar) * 56;
            // build segments
            return (
              <div key={b.i}
                onMouseEnter={() => setHoverBucket(b)}
                style={{
                  flex: 1, height: '100%', display: 'flex', flexDirection: 'column-reverse',
                  cursor: 'pointer', position: 'relative',
                  opacity: hoverBucket && hoverBucket.i !== b.i ? 0.55 : 1,
                  transform: hoverBucket?.i === b.i ? 'scaleY(1.06)' : 'scaleY(1)',
                  transformOrigin: 'bottom',
                  transition: 'transform .12s, opacity .12s',
                }}
              >
                {[...filters].sort().map(ns => {
                  const segH = tot > 0 ? (b[ns] / tot) * h : 0;
                  return <div key={ns} style={{ height: segH, background: nsColors[ns], borderRadius: 1 }} />;
                })}
              </div>
            );
          })}
        </div>
        <div style={{
          display: 'flex', justifyContent: 'space-between', marginTop: 4,
          fontFamily: dS58.mono, fontSize: 9, color: D58.ink4, fontWeight: 600,
        }}>
          <span>−60m</span>
          <span>−45m</span>
          <span>−30m</span>
          <span>−15m</span>
          <span>now</span>
        </div>
        {hoverBucket && (
          <div style={{
            position: 'absolute', right: 22, top: 86,
            background: D58.ink, color: D58.paper, fontFamily: dS58.mono,
            fontSize: 10, padding: '6px 9px', borderRadius: 6, lineHeight: 1.5,
            pointerEvents: 'none',
          }}>
            <div style={{ color: D58.honey, fontWeight: 700 }}>−{59 - hoverBucket.i}m</div>
            {[...filters].map(ns => (
              <div key={ns}>
                <span style={{ color: nsColors[ns] }}>■</span> {ns}: {hoverBucket[ns]}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* feed */}
      <div className="d-feed" style={{
        maxHeight: 360, overflow: 'auto',
        fontFamily: dS58.mono, fontSize: 11.5, color: D58.ink2,
      }}>
        {visibleFeed.map((e, i) => (
          <div key={i}
            onClick={() => setExpanded(expanded === i ? null : i)}
            className="d-row"
            style={{
              padding: '7px 16px', borderBottom: `1px dashed ${D58.ruleFaint}`,
              cursor: 'pointer',
              display: 'flex', alignItems: 'flex-start', gap: 10,
              background: expanded === i ? D58.paperSoft : 'transparent',
            }}
          >
            <span style={{ color: D58.ink4, fontWeight: 600, minWidth: 56 }}>{e.ts.slice(0, 5)}</span>
            <D_NSPill ns={e.ns} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ color: D58.ink, fontWeight: 500 }}>{e.detail}</div>
              {expanded === i && (
                <pre style={{
                  margin: '6px 0 2px', padding: '8px 10px',
                  background: D58.ink, color: D58.honey,
                  fontSize: 10.5, borderRadius: 6, fontFamily: dS58.mono,
                  whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                }}>
{JSON.stringify(e.payload, null, 2)}
                </pre>
              )}
            </div>
            <span style={{ color: D58.ink4, fontWeight: 600 }}>{expanded === i ? '⌃' : '⌄'}</span>
          </div>
        ))}
      </div>

      <div style={{
        padding: '8px 16px', borderTop: `1px solid ${D58.ruleSoft}`, background: D58.paperSoft,
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        fontFamily: dS58.mono, fontSize: 11, fontWeight: 600,
      }}>
        <span style={{ color: D58.ink3 }}>showing {visibleFeed.length} most recent · click row to expand</span>
        <a href="#" style={{ color: D58.ink, fontWeight: 700, textDecoration: 'none' }}>View full audit log →</a>
      </div>
    </DC58>
  );
}

// ═══ WIDGET 8: FAILURE SCATTER ═════════════════════════════

function W8_Failure() {
  const dat = window.HIVE_DASH.failures;
  const sumr = window.HIVE_DASH.failuresSummary;
  const ent = window.HIVE_DASH.entitiesY;
  const [hover, setHover] = React.useState(null);

  const W = 1100, H = 200;
  const padL = 90, padR = 18, padT = 14, padB = 28;
  const innerW = W - padL - padR, innerH = H - padT - padB;
  const minutes = 1440; // last 24h

  const x = m => padL + (m / minutes) * innerW;
  const y = e => padT + (ent.indexOf(e) + 0.5) * (innerH / ent.length);

  const sizeMap = { s: 3.5, m: 5.5, l: 8 };
  const typeColor = {
    'rate.limit': D58.amber,
    'retry':      D58.ink3,
    'crash':      D58.accent,
    'escalate':   D58.honey,
  };

  return (
    <DC58 title="Failure scatter"
      sub="last 24h · y = entity · size = severity · color = type">
      <div style={{
        padding: '12px 16px', borderBottom: `1px dashed ${D58.ruleSoft}`,
        display: 'flex', alignItems: 'center', gap: 16,
        fontFamily: dS58.mono, fontSize: 12, color: D58.ink2, fontWeight: 600,
      }}>
        <span style={{ color: D58.ink, fontWeight: 700 }}>
          <b style={{ color: D58.accent, fontSize: 14 }}>{sumr.lastHour}</b> failures · last hour
        </span>
        <span style={{ color: D58.ink4 }}>·</span>
        <span style={{ color: D58.ink, fontWeight: 700 }}>
          <b style={{ color: D58.honey, fontSize: 14 }}>{sumr.pendingEscalations}</b> escalation pending
        </span>
        <span style={{ color: D58.ink4 }}>·</span>
        {sumr.longestStreak ? (
          <span>longest streak: <b style={{ color: D58.accent }}>/m:{sumr.longestStreak.entity}</b> ({sumr.longestStreak.count} in {sumr.longestStreak.window})</span>
        ) : (
          <span style={{ color: D58.ink4 }}>longest streak: <b style={{ color: D58.ink3 }}>none</b></span>
        )}
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 12 }}>
          {Object.entries(typeColor).map(([k, c]) => (
            <span key={k} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 10 }}>
              <span style={{ width: 8, height: 8, background: c, borderRadius: '50%' }} /> {k}
            </span>
          ))}
        </span>
      </div>

      <div style={{ padding: '8px 0' }}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ width: '100%', height: 220 }}>
        {/* horizontal swim lanes */}
        {ent.map((e, i) => (
          <g key={e}>
            <line x1={padL} x2={W - padR} y1={y(e)} y2={y(e)} stroke={D58.ruleFaint} strokeDasharray="2 4" />
            <text x={padL - 8} y={y(e) + 4} fontSize="11" fontFamily={dS58.mono} fontWeight="700"
              fill={e === sumr.longestStreak?.entity ? D58.accent : D58.ink2} textAnchor="end">
              /m:{e}
            </text>
          </g>
        ))}

        {/* vertical hour ticks */}
        {[0, 6, 12, 18, 24].map(h => (
          <g key={h}>
            <line x1={x(h * 60)} x2={x(h * 60)} y1={padT} y2={padT + innerH}
              stroke={D58.ruleFaint} strokeDasharray="2 4" />
            <text x={x(h * 60)} y={H - 10} fontSize="9" fontFamily={dS58.mono} fontWeight="600"
              fill={D58.ink4} textAnchor="middle">
              {h === 24 ? 'now' : `${String(h).padStart(2,'0')}:00`}
            </text>
          </g>
        ))}

        {/* 14:00 systemic-event highlight */}
        <rect x={x(840) - 6} y={padT} width="12" height={innerH}
          fill={D58.accent} opacity="0.07" />
        <text x={x(840)} y={padT - 2} fontSize="9" fontFamily={dS58.mono} fontWeight="700"
          fill={D58.accent} textAnchor="middle">14:00 systemic</text>

        {/* dots */}
        {dat.map((f, i) => {
          const isHover = hover === i;
          return (
            <circle key={i}
              cx={x(f.t)} cy={y(f.entity)}
              r={sizeMap[f.size]}
              fill={typeColor[f.type]}
              opacity={isHover ? 1 : 0.85}
              stroke={isHover ? D58.ink : D58.paper}
              strokeWidth={isHover ? 2 : 1}
              style={{ cursor: 'pointer', transition: 'r .12s' }}
              onMouseEnter={() => setHover(i)}
              onMouseLeave={() => setHover(null)}
            />
          );
        })}
      </svg>
      </div>

      {hover != null && (
        <div style={{
          position: 'absolute', right: 16, bottom: 12,
          background: D58.ink, color: D58.paper, fontFamily: dS58.mono,
          fontSize: 10, padding: '7px 10px', borderRadius: 6, lineHeight: 1.55,
        }}>
          {(() => {
            const f = dat[hover];
            const hrs = Math.floor(f.t / 60), min = f.t % 60;
            return (
              <>
                <div style={{ color: D58.honey, fontWeight: 700 }}>/m:{f.entity} · {String(hrs).padStart(2,'0')}:{String(min).padStart(2,'0')}</div>
                <div>type: <b style={{ color: typeColor[f.type] }}>{f.type}</b></div>
                <div>severity: {f.size === 'l' ? 'high' : f.size === 'm' ? 'med' : 'low'}</div>
                <div style={{ color: D58.ink5 }}>click to open detail →</div>
              </>
            );
          })()}
        </div>
      )}
    </DC58>
  );
}

window.W5_BubbleMatrix = W5_BubbleMatrix;
window.W6_CacheHit = W6_CacheHit;
window.W7_AuditLog = W7_AuditLog;
window.W8_Failure = W8_Failure;
})();
