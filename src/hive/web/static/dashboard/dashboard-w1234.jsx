// Widgets 1–4: Cost ribbon, Health strip, Sankey, Token burn

const { D, dStyles, D_Card, D_StateDot, D_PriorityPill, D_Hex } = window;

// ═══ WIDGET 1: COST BURN RIBBON ════════════════════════════

function W1_CostRibbon() {
  const data = window.HIVE_DASH.cost30;
  const [hover, setHover] = React.useState(null);
  const ref = React.useRef(null);

  const W = 760, H = 180;
  const padL = 36, padR = 14, padT = 14, padB = 24;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;
  const maxY = Math.max(...data.map(d => Math.max(d.cost, d.median + d.stdev * 2))) * 1.1;

  const x = i => padL + (i / (data.length - 1)) * innerW;
  const y = v => padT + innerH - (v / maxY) * innerH;

  // envelope path (median ± 1 stdev) — area
  const upper = data.map((d, i) => `${x(i)},${y(d.median + d.stdev)}`).join(' ');
  const lower = data.map((d, i) => `${x(i)},${y(d.median - d.stdev)}`).reverse().join(' ');
  const envPath = `M ${upper} L ${lower} Z`;
  const medianPath = data.map((d, i) => `${i ? 'L' : 'M'} ${x(i)} ${y(d.median)}`).join(' ');
  const costArea = `M ${x(0)},${y(0)} ` + data.map((d, i) => `L ${x(i)},${y(d.cost)}`).join(' ') + ` L ${x(data.length - 1)},${y(0)} Z`;
  const costLine = data.map((d, i) => `${i ? 'L' : 'M'} ${x(i)} ${y(d.cost)}`).join(' ');

  const onMove = (e) => {
    const rect = ref.current.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * W;
    const i = Math.max(0, Math.min(data.length - 1, Math.round((px - padL) / innerW * (data.length - 1))));
    setHover({ i, px: x(i) });
  };

  const today = data[data.length - 1];
  const yest  = data[data.length - 2];
  const delta = today.cost - yest.cost;
  const mtd = data.slice(-Math.min(today.ts.split('-')[2] | 0 || 30, data.length))
    .reduce((s, d) => s + d.cost, 0);

  return (
    <D_Card
      title="Cost burn"
      sub="last 30 days · daily · envelope = 30d median ±σ"
      style={{ flex: '1 1 60%' }}
    >
      <div style={{ padding: '14px 16px 6px', display: 'flex', alignItems: 'baseline', gap: 18 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
            <span style={{ fontFamily: dStyles.display, fontSize: 32, fontWeight: 800, color: D.ink, letterSpacing: -1, lineHeight: 1 }}>
              ${today.cost.toFixed(2)}
            </span>
            <span style={{
              fontFamily: dStyles.mono, fontSize: 12, fontWeight: 700,
              color: delta >= 0 ? D.accent : D.sage,
              padding: '1px 6px', background: delta >= 0 ? D.accentSoft : D.sageSoft,
              borderRadius: 4,
            }}>
              {delta >= 0 ? '▲' : '▼'} ${Math.abs(delta).toFixed(2)}
            </span>
          </div>
          <div style={{ fontSize: 11, color: D.ink3, fontWeight: 600, marginTop: 3 }}>
            today vs yesterday
          </div>
        </div>
        <div>
          <div style={{ fontFamily: dStyles.display, fontSize: 18, fontWeight: 700, color: D.ink2 }}>
            ${mtd.toFixed(2)}
          </div>
          <div style={{ fontSize: 11, color: D.ink3, fontWeight: 600, marginTop: 1 }}>month-to-date</div>
        </div>
        <div style={{ marginLeft: 'auto', textAlign: 'right' }}>
          <div style={{ fontFamily: dStyles.mono, fontSize: 10, color: D.ink4, fontWeight: 600 }}>
            anthropic max covered $—
          </div>
          <div style={{ fontFamily: dStyles.mono, fontSize: 10, color: D.ink4, fontWeight: 600 }}>
            saved vs api: ~$420 mtd
          </div>
        </div>
      </div>

      <svg
        ref={ref}
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        style={{ width: '100%', height: 200, display: 'block', cursor: 'crosshair' }}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
      >
        {/* horizontal grid */}
        {[0.25, 0.5, 0.75, 1].map(t => (
          <line key={t} x1={padL} x2={W - padR} y1={padT + innerH * (1 - t)} y2={padT + innerH * (1 - t)}
            stroke={D.ruleFaint} strokeDasharray="2 4" />
        ))}
        {[0.25, 0.5, 0.75, 1].map(t => (
          <text key={t} x={padL - 6} y={padT + innerH * (1 - t) + 3}
            fontSize="9" fontFamily={dStyles.mono} fill={D.ink4} textAnchor="end" fontWeight="600">
            ${(maxY * t).toFixed(0)}
          </text>
        ))}

        {/* envelope */}
        <path d={envPath} fill={D.honeySoft} opacity="0.7" />
        <path d={medianPath} stroke={D.honey} strokeWidth="1.2" fill="none" strokeDasharray="3 3" opacity="0.7" />

        {/* cost area */}
        <path d={costArea} fill={D.accent} opacity="0.12" />
        <path d={costLine} stroke={D.ink} strokeWidth="1.5" fill="none" />

        {/* anomaly dots */}
        {data.map((d, i) => {
          const out = d.cost > d.median + d.stdev;
          if (!out) return null;
          return (
            <circle key={i} cx={x(i)} cy={y(d.cost)} r="3.5"
              fill={D.accent} stroke={D.paper} strokeWidth="1.5" />
          );
        })}
        {/* today marker */}
        <circle cx={x(data.length - 1)} cy={y(today.cost)} r="4" fill={D.ink} stroke={D.paper} strokeWidth="2" />

        {/* x labels — every 5 days */}
        {data.map((d, i) => i % 5 === 0 || i === data.length - 1 ? (
          <text key={i} x={x(i)} y={H - 6} fontSize="9" fontFamily={dStyles.mono}
            fill={D.ink4} textAnchor="middle" fontWeight="600">
            {i === data.length - 1 ? 'today' : `−${data.length - 1 - i}d`}
          </text>
        ) : null)}

        {/* hover scrubber */}
        {hover && (
          <g>
            <line x1={hover.px} x2={hover.px} y1={padT} y2={padT + innerH}
              stroke={D.ink} strokeWidth="1" strokeDasharray="2 2" opacity="0.5" />
            <circle cx={hover.px} cy={y(data[hover.i].cost)} r="4.5"
              fill={D.honey} stroke={D.ink} strokeWidth="1.5" />
          </g>
        )}
      </svg>

      {/* tooltip */}
      {hover && (() => {
        const d = data[hover.i];
        const out = d.cost > d.median + d.stdev;
        const dev = ((d.cost - d.median) / d.median * 100).toFixed(0);
        return (
          <div style={{
            position: 'absolute', left: 16, bottom: 10,
            background: D.ink, color: D.paper, fontFamily: dStyles.mono,
            fontSize: 10, padding: '6px 9px', borderRadius: 6, lineHeight: 1.55,
            border: `1px solid ${D.ink2}`, pointerEvents: 'none',
          }}>
            <div style={{ fontWeight: 700, color: D.honey }}>{d.day} · {d.ts}</div>
            <div>cost: <span style={{ color: out ? D.honey : D.paper, fontWeight: 700 }}>${d.cost.toFixed(2)}</span> {out ? '· outside envelope' : '· inside envelope'}</div>
            <div>median: ${d.median.toFixed(2)} (±${d.stdev.toFixed(2)})</div>
            <div>{dev > 0 ? '+' : ''}{dev}% vs median</div>
          </div>
        );
      })()}
    </D_Card>
  );
}

// ═══ WIDGET 2: HEALTH HEARTBEAT STRIP ══════════════════════

function W2_Health() {
  const strips = window.HIVE_DASH.health;
  const [hover, setHover] = React.useState(null);
  return (
    <D_Card title="System health" sub="last 60 minutes · 1 bar = 1 min" style={{ flex: '1 1 40%' }}>
      <div style={{ padding: '14px 16px 14px', display: 'flex', flexDirection: 'column', gap: 11 }}>
        {strips.map(s => (
          <div key={s.name} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{
              minWidth: 110, fontFamily: dStyles.mono, fontSize: 11.5, fontWeight: 700,
              color: D.ink2,
              display: 'inline-flex', alignItems: 'center', gap: 6,
              padding: '3px 7px', borderRadius: 6,
              background: s.lit ? D.accentSoft : 'transparent',
              border: s.lit ? `1px solid ${D.accent}55` : '1px solid transparent',
              boxShadow: s.lit ? `0 0 10px ${D.accent}33` : 'none',
            }}>
              <D_StateDot state={s.lit ? 'failed' : 'ok'} size={6} />
              {s.name}
            </div>
            <div style={{ flex: 1, display: 'flex', gap: 1, height: 22 }}>
              {s.bars.map((b, i) => {
                const map = { ok: D.sage, degraded: D.amber, failed: D.accent, gap: D.paperDeep };
                return (
                  <div
                    key={i}
                    onMouseEnter={() => setHover({ name: s.name, i, status: b })}
                    onMouseLeave={() => setHover(null)}
                    style={{
                      flex: 1, background: map[b], borderRadius: 1.5,
                      cursor: 'pointer',
                      transition: 'transform .12s',
                      transform: hover?.name === s.name && hover.i === i ? 'scaleY(1.15)' : 'scaleY(1)',
                      opacity: hover && (hover.name !== s.name || hover.i !== i) ? 0.55 : 1,
                    }}
                  />
                );
              })}
            </div>
            <div style={{
              minWidth: 124, textAlign: 'right',
              fontFamily: dStyles.mono, fontSize: 10.5, color: D.ink3, fontWeight: 600,
            }}>{s.summary}</div>
          </div>
        ))}
      </div>
      {hover && (
        <div style={{
          position: 'absolute', right: 16, bottom: 10,
          background: D.ink, color: D.paper, fontFamily: dStyles.mono,
          fontSize: 10, padding: '6px 9px', borderRadius: 6, lineHeight: 1.5,
        }}>
          <div style={{ color: D.honey, fontWeight: 700 }}>{hover.name} · −{59 - hover.i}m</div>
          <div>status: <span style={{ fontWeight: 700,
            color: hover.status === 'ok' ? D.sage : hover.status === 'degraded' ? D.honey : hover.status === 'failed' ? D.accent : D.ink5
          }}>{hover.status}</span></div>
        </div>
      )}
    </D_Card>
  );
}

// ═══ WIDGET 3: WORKLOAD CFD (Cumulative Flow Diagram) ══════

function W3_CFD() {
  const cfd = window.HIVE_DASH.cfd;
  const b = window.HIVE_DASH.p0p1Backlog;
  const [hover, setHover] = React.useState(null);
  const ref = React.useRef(null);

  const W = 1100, H = 230;
  const padL = 44, padR = 18, padT = 22, padB = 28;
  const innerW = W - padL - padR, innerH = H - padT - padB;

  const pts = cfd.points;
  const N = pts.length;
  const yMax = Math.max(...pts.map(p => p.total)) * 1.04;
  const x = i => padL + (i / (N - 1)) * innerW;
  const y = v => padT + innerH - (v / yMax) * innerH;

  // stack order, bottom → top: completed, inProgress, pending
  const stackKeys = ['completed', 'inProgress', 'pending'];
  const colors = {
    completed:  D.sage,
    inProgress: D.amber,
    pending:    D.accent,
  };
  const labels = {
    completed:  'completed',
    inProgress: 'in-progress',
    pending:    'pending',
  };

  // build cumulative top edges per stack
  const tops = {}; // key -> array of {i, val}
  let running = pts.map(() => 0);
  for (const k of stackKeys) {
    running = pts.map((p, i) => running[i] + p[k]);
    tops[k] = running.slice();
  }

  // path for a band: along top edge forward, then along bottom edge backward
  // (kept for anomaly edge highlights only — bars handle the main story)
  function edgeFromArr(arr, fromI, toI) {
    let d = `M ${barX(fromI) + barW / 2} ${y(arr[fromI])}`;
    for (let i = fromI + 1; i <= toI; i++) d += ` L ${barX(i) + barW / 2} ${y(arr[i])}`;
    return d;
  }

  // bar geometry — one stacked bar per time bucket
  const barW = (innerW / N) * 0.78;
  const barX = i => padL + (i / N) * innerW + (innerW / N - barW) / 2;

  const completedTop = tops.completed;
  const inProgTop    = tops.inProgress;
  const pendingTop   = tops.pending;

  // x ticks: one per day
  const dayTicks = cfd.dayBoundaries.map((idx, d) => ({
    i: idx, label: `D${d + 1}`,
  }));

  // y ticks
  const yTicks = 4;
  const yStep = Math.ceil(yMax / yTicks / 50) * 50;

  function onMove(e) {
    const r = ref.current.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    if (px < padL || px > padL + innerW) { setHover(null); return; }
    const i = Math.min(N - 1, Math.max(0, Math.floor(((px - padL) / innerW) * N)));
    setHover({ i, px: barX(i) + barW / 2 });
  }

  const hov = hover ? pts[hover.i] : null;
  const prevDayIdx = hov ? Math.max(0, hov.day * 6 - 1) : 0;
  const prevPt = hov ? pts[Math.max(0, hov.i - 6)] : null;

  return (
    <D_Card
      title="Workload flow"
      sub="last 7 days · stacked bars = cumulative tasks · bottleneck signal: a widening amber segment"
      right={
        <div style={{
          display: 'flex', alignItems: 'center', gap: 9,
          padding: '4px 10px',
          background: b.count > 0 ? D.accentSoft : 'transparent',
          border: `1px solid ${b.count > 0 ? D.accent + '55' : D.ruleSoft}`,
          borderRadius: 999,
        }}>
          <span style={{ fontFamily: dStyles.mono, fontSize: 10, fontWeight: 800, color: b.count > 0 ? D.accent : D.ink2 }}>P0/P1 backlog</span>
          <span style={{ fontFamily: dStyles.display, fontWeight: 800, fontSize: 18, color: b.count > 0 ? D.accent : D.ink, lineHeight: 1 }}>{b.count}</span>
          <span style={{ fontFamily: dStyles.mono, fontSize: 10, fontWeight: 700, color: b.count > 0 ? D.accent : D.ink3 }}>
            {b.deltaYesterday > 0 ? '▲' : '▼'}{Math.abs(b.deltaYesterday)} vs yest
          </span>
        </div>
      }
    >
      <div style={{ position: 'relative' }}>
      <svg ref={ref} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
        style={{ width: '100%', height: 240, display: 'block', cursor: 'crosshair' }}
        onMouseMove={onMove} onMouseLeave={() => setHover(null)}>

        {/* y grid */}
        {Array.from({ length: yTicks + 1 }, (_, k) => {
          const v = k * yStep;
          if (v > yMax) return null;
          return (
            <g key={k}>
              <line x1={padL} y1={y(v)} x2={padL + innerW} y2={y(v)}
                stroke={D.ruleSoft} strokeWidth="1" strokeDasharray="2 4" />
              <text x={padL - 8} y={y(v) + 3} fontSize="9.5" fontFamily={dStyles.mono} fontWeight="600"
                textAnchor="end" fill={D.ink3}>{v}</text>
            </g>
          );
        })}

        {/* day boundaries */}
        {dayTicks.map(t => (
          <line key={t.i} x1={x(t.i)} y1={padT} x2={x(t.i)} y2={padT + innerH}
            stroke={D.ruleSoft} strokeWidth="1" strokeDasharray="1 5" opacity="0.7" />
        ))}

        {/* stacked bars — bottom to top: completed, inProgress, pending */}
        {pts.map((p, i) => {
          const x0 = barX(i);
          const yC0 = y(0),                  yC1 = y(completedTop[i]);
          const yI0 = y(completedTop[i]),    yI1 = y(inProgTop[i]);
          const yP0 = y(inProgTop[i]),       yP1 = y(pendingTop[i]);
          const isHover = hov && hov.i === pts[i].i;
          const inAnomIP = i >= cfd.anomalies[0].from && i <= cfd.anomalies[0].to;
          const inAnomP  = i >= cfd.anomalies[1].from && i <= cfd.anomalies[1].to;
          return (
            <g key={i} opacity={hov && !isHover ? 0.55 : 1}>
              {p.completed > 0 && (
                <rect x={x0} y={yC1} width={barW} height={Math.max(0, yC0 - yC1)}
                  fill={colors.completed} opacity={isHover ? 0.85 : 0.7} />
              )}
              {p.inProgress > 0 && (
                <rect x={x0} y={yI1} width={barW} height={Math.max(0, yI0 - yI1)}
                  fill={colors.inProgress}
                  opacity={isHover ? 0.95 : (inAnomIP ? 0.85 : 0.72)}
                  stroke={inAnomIP ? colors.inProgress : 'none'}
                  strokeWidth={inAnomIP ? 0.5 : 0} />
              )}
              {p.pending > 0 && (
                <rect x={x0} y={yP1} width={barW} height={Math.max(0, yP0 - yP1)}
                  fill={colors.pending}
                  opacity={isHover ? 0.95 : (inAnomP ? 0.78 : 0.6)}
                  stroke={inAnomP ? colors.pending : 'none'}
                  strokeWidth={inAnomP ? 0.5 : 0} />
              )}
            </g>
          );
        })}

        {/* anomaly trend lines — thin tracker on top edge of widening band */}
        {cfd.anomalies.map((a, i) => {
          const arr = a.band === 'inProgress' ? inProgTop : pendingTop;
          const c   = a.band === 'inProgress' ? colors.inProgress : colors.pending;
          return (
            <g key={i}>
              <path d={edgeFromArr(arr, a.from, a.to)} fill="none" stroke={c} strokeWidth="2" opacity="0.95"
                strokeLinecap="round" strokeLinejoin="round" />
            </g>
          );
        })}

        {/* annotations near the right edge for each anomaly */}
        {(() => {
          // place inProgress annotation pointing to in-progress band, pending on top
          const lastI = N - 1;
          const ipY = y((completedTop[lastI] + inProgTop[lastI]) / 2);
          const pY  = y((inProgTop[lastI] + pendingTop[lastI]) / 2);
          return (
            <g fontFamily={dStyles.mono} fontSize="10.5" fontWeight="700">
              <rect x={padL + innerW - 270} y={ipY - 11} width="260" height="18" rx="4"
                fill={D.amber} opacity="0.18" />
              <text x={padL + innerW - 6} y={ipY + 3} textAnchor="end" fill={D.ink}>
                ⚠ in-progress growing — possible bottleneck
              </text>
              <rect x={padL + innerW - 250} y={pY - 11} width="240" height="18" rx="4"
                fill={D.accent} opacity="0.16" />
              <text x={padL + innerW - 6} y={pY + 3} textAnchor="end" fill={D.accent}>
                ⚠ pending growing — capacity signal
              </text>
            </g>
          );
        })()}

        {/* day labels along bottom */}
        {dayTicks.map(t => (
          <text key={t.i} x={x(t.i)} y={padT + innerH + 16} fontSize="10" fontFamily={dStyles.mono} fontWeight="700"
            textAnchor="middle" fill={D.ink3}>{t.label}</text>
        ))}

        {/* hover scrubber */}
        {hov && (
          <g pointerEvents="none">
            <rect x={barX(hover.i) - 1.5} y={padT} width={barW + 3} height={innerH}
              fill="none" stroke={D.ink} strokeWidth="1.2" opacity="0.5" rx="1" />
          </g>
        )}
      </svg>

      {hov && prevPt && (
        <div style={{
          position: 'absolute',
          left: Math.min(hover.px / W * 100, 78) + '%',
          top: 14,
          transform: hover.px / W > 0.7 ? 'translateX(-100%)' : 'translateX(8px)',
          background: D.ink, color: D.paper, fontFamily: dStyles.mono,
          fontSize: 10, padding: '7px 10px', borderRadius: 6, lineHeight: 1.55,
          pointerEvents: 'none', minWidth: 170,
        }}>
          <div style={{ color: D.honey, fontWeight: 700, marginBottom: 3 }}>{hov.label}</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr auto auto', gap: '0 8px' }}>
            <span style={{ color: colors.pending, fontWeight: 700 }}>● pending</span>
            <span>{hov.pending}</span>
            <span style={{ color: hov.pending - prevPt.pending > 0 ? D.accent : D.sage }}>
              {hov.pending - prevPt.pending >= 0 ? '+' : ''}{hov.pending - prevPt.pending}
            </span>

            <span style={{ color: colors.inProgress, fontWeight: 700 }}>● in-progress</span>
            <span>{hov.inProgress}</span>
            <span style={{ color: hov.inProgress - prevPt.inProgress > 0 ? D.accent : D.sage }}>
              {hov.inProgress - prevPt.inProgress >= 0 ? '+' : ''}{hov.inProgress - prevPt.inProgress}
            </span>

            <span style={{ color: colors.completed, fontWeight: 700 }}>● completed</span>
            <span>{hov.completed}</span>
            <span style={{ color: D.sage }}>
              +{hov.completed - prevPt.completed}
            </span>
          </div>
          <div style={{ color: D.ink3, marginTop: 3, fontSize: 9.5 }}>net Δ vs −24h</div>
        </div>
      )}
      </div>

      {/* legend strip */}
      <div style={{ display: 'flex', padding: '6px 16px 14px', alignItems: 'center', gap: 16 }}>
        <div style={{ display: 'flex', gap: 14, alignItems: 'center', fontFamily: dStyles.mono, fontSize: 11, color: D.ink2, fontWeight: 700 }}>
          {stackKeys.slice().reverse().map(k => (
            <span key={k} style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
              <span style={{ width: 12, height: 12, background: colors[k], opacity: 0.5, borderRadius: 2, border: `1px solid ${colors[k]}` }} />
              {labels[k]}
            </span>
          ))}
        </div>
        <div style={{ marginLeft: 'auto', fontFamily: dStyles.mono, fontSize: 10.5, color: D.ink3, fontWeight: 600 }}>
          cancelled in last 7 days: {cfd.cancelled7d}
        </div>
      </div>
    </D_Card>
  );
}

// ═══ WIDGET 4: TOKEN BURN ═════════════════════════════════

function W4_TokenBurn({ range }) {
  const data = window.HIVE_DASH.burn[range] || window.HIVE_DASH.burn['24h'];
  const events = window.HIVE_DASH.burnEvents[range] || [];
  const [yMode, setYMode] = React.useState('tokens'); // tokens | cost | normalized
  const [hover, setHover] = React.useState(null);
  const [hoverEvt, setHoverEvt] = React.useState(null);
  const ref = React.useRef(null);

  const W = 1100, H = 280;
  const padL = 50, padR = 60, padT = 18, padB = 32;
  const innerW = W - padL - padR, innerH = H - padT - padB;

  const series = ['cacheRead', 'cacheCreate', 'input', 'output'];
  const colors = {
    cacheRead:   D.vault,
    cacheCreate: D.ink4,
    input:       D.ink3,
    output:      D.honey,
  };
  const labels = {
    cacheRead: 'cache-read', cacheCreate: 'cache-create', input: 'input', output: 'output',
  };

  let stacks;
  let maxStack = 0;
  if (yMode === 'tokens') {
    stacks = data.map(d => {
      let acc = 0;
      const out = {};
      for (const k of series) {
        out[k] = { y0: acc, y1: acc + d[k] };
        acc += d[k];
      }
      maxStack = Math.max(maxStack, acc);
      return out;
    });
  } else if (yMode === 'cost') {
    const costPerTok = { cacheRead: 0.0000003, cacheCreate: 0.000003, input: 0.000003, output: 0.000015 };
    stacks = data.map(d => {
      let acc = 0;
      const out = {};
      for (const k of series) {
        const v = d[k] * costPerTok[k];
        out[k] = { y0: acc, y1: acc + v };
        acc += v;
      }
      maxStack = Math.max(maxStack, acc);
      return out;
    });
  } else {
    // normalized %
    stacks = data.map(d => {
      const total = series.reduce((a, k) => a + d[k], 0);
      let acc = 0;
      const out = {};
      for (const k of series) {
        const v = d[k] / total;
        out[k] = { y0: acc, y1: acc + v };
        acc += v;
      }
      return out;
    });
    maxStack = 1;
  }

  const x = i => padL + (i / (data.length - 1)) * innerW;
  const y = v => padT + innerH - (v / maxStack) * innerH;

  // cost line on right axis
  const maxCost = Math.max(...data.map(d => d.cost));
  const yCost = v => padT + innerH - (v / (maxCost * 1.2)) * innerH;
  const costPath = data.map((d, i) => `${i ? 'L' : 'M'} ${x(i)} ${yCost(d.cost)}`).join(' ');

  function areaPath(key) {
    const top = data.map((_, i) => `${x(i)},${y(stacks[i][key].y1)}`).join(' ');
    const bot = data.map((_, i) => `${x(i)},${y(stacks[i][key].y0)}`).reverse().join(' ');
    return `M ${top} L ${bot} Z`;
  }

  const onMove = (e) => {
    const rect = ref.current.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * W;
    const i = Math.max(0, Math.min(data.length - 1, Math.round((px - padL) / innerW * (data.length - 1))));
    setHover({ i, px: x(i) });
  };

  function fmt(v) {
    if (yMode === 'cost') return `$${v.toFixed(3)}`;
    if (yMode === 'normalized') return `${(v * 100).toFixed(0)}%`;
    if (v > 1000_000) return `${(v / 1_000_000).toFixed(1)}M`;
    if (v > 1000) return `${Math.round(v / 1000)}k`;
    return v.toFixed(0);
  }

  return (
    <D_Card
      title="Token burn over time"
      sub={`stacked: cache-read · cache-create · input · output · range = ${range}`}
      right={
        <div style={{ display: 'flex', gap: 0, padding: 3, background: D.paperSoft, border: `1px solid ${D.ruleSoft}`, borderRadius: 9 }}>
          {[
            { k: 'tokens', label: 'tokens' },
            { k: 'cost', label: '$ cost' },
            { k: 'normalized', label: 'normalized %' },
          ].map(b => (
            <button key={b.k} className="d-btn" onClick={() => setYMode(b.k)} style={{
              padding: '4px 10px', borderRadius: 6,
              background: yMode === b.k ? D.ink : 'transparent',
              color: yMode === b.k ? D.paper : D.ink2,
              border: 'none', fontFamily: dStyles.mono, fontSize: 11, fontWeight: 700, cursor: 'pointer',
            }}>{b.label}</button>
          ))}
        </div>
      }
    >
      <div style={{ position: 'relative' }}>
      <svg ref={ref} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
        style={{ width: '100%', height: 320, display: 'block', cursor: 'crosshair' }}
        onMouseMove={onMove} onMouseLeave={() => setHover(null)}>

        {/* y grid */}
        {[0, 0.25, 0.5, 0.75, 1].map(t => (
          <g key={t}>
            <line x1={padL} x2={W - padR} y1={padT + innerH * (1 - t)} y2={padT + innerH * (1 - t)}
              stroke={D.ruleFaint} strokeDasharray="2 4" />
            <text x={padL - 6} y={padT + innerH * (1 - t) + 3} fontSize="9"
              fontFamily={dStyles.mono} fill={D.ink4} textAnchor="end" fontWeight="600">
              {fmt(maxStack * t)}
            </text>
          </g>
        ))}
        {/* right axis labels — cost */}
        {yMode !== 'normalized' && [0, 0.5, 1].map(t => (
          <text key={t} x={W - padR + 6} y={padT + innerH * (1 - t) + 3}
            fontSize="9" fontFamily={dStyles.mono} fill={D.honey} fontWeight="700">
            ${(maxCost * 1.2 * t).toFixed(2)}
          </text>
        ))}

        {/* event annotation lines — under the data so they don't dominate */}
        {events.map((e, idx) => {
          const cmap = { amber: D.amber, sage: D.sage, ink: D.ink2, accent: D.accent };
          return (
            <g key={idx}
              onMouseEnter={() => setHoverEvt(e)}
              onMouseLeave={() => setHoverEvt(null)}
              style={{ cursor: 'pointer' }}
            >
              <line x1={x(e.at)} x2={x(e.at)} y1={padT} y2={padT + innerH}
                stroke={cmap[e.color] || D.ink3} strokeWidth="1" strokeDasharray="3 3" opacity="0.5" />
              <circle cx={x(e.at)} cy={padT - 4} r="4" fill={cmap[e.color] || D.ink3}
                stroke={D.paper} strokeWidth="1.5" />
            </g>
          );
        })}

        {/* stacked areas */}
        {series.map(k => (
          <path key={k} d={areaPath(k)} fill={colors[k]} opacity={k === 'cacheRead' ? 0.55 : k === 'output' ? 0.85 : 0.6} />
        ))}

        {/* secondary cost line */}
        {yMode !== 'normalized' && (
          <path d={costPath} stroke={D.honey} strokeWidth="2" fill="none" strokeLinejoin="round" />
        )}

        {/* x labels — sample evenly */}
        {data.map((d, i) => {
          const step = Math.ceil(data.length / 8);
          if (i % step !== 0 && i !== data.length - 1) return null;
          return (
            <text key={i} x={x(i)} y={H - 12} fontSize="9" fontFamily={dStyles.mono}
              fill={D.ink4} textAnchor="middle" fontWeight="600">
              {d.label}
            </text>
          );
        })}

        {/* hover scrubber */}
        {hover && (
          <line x1={hover.px} x2={hover.px} y1={padT} y2={padT + innerH}
            stroke={D.ink} strokeWidth="1" strokeDasharray="2 2" opacity="0.6" />
        )}
      </svg>

      {/* legend */}
      <div style={{
        display: 'flex', gap: 14, padding: '8px 16px 12px',
        fontFamily: dStyles.mono, fontSize: 10, color: D.ink3, fontWeight: 700,
      }}>
        {series.map(k => (
          <span key={k} style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
            <span style={{ width: 10, height: 10, background: colors[k], borderRadius: 2,
              opacity: k === 'cacheRead' ? 0.55 : k === 'output' ? 0.85 : 0.6 }} />
            {labels[k]}
          </span>
        ))}
        {yMode !== 'normalized' && (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
            <span style={{ width: 14, height: 2, background: D.honey }} />
            $ cost (right axis)
          </span>
        )}
        <span style={{ marginLeft: 'auto' }}>
          events: {events.length} in window
        </span>
      </div>

      {/* tooltip */}
      {hover && (
        <div style={{
          position: 'absolute', left: 50 + (hover.px / W) * (100), top: 22,
          background: D.ink, color: D.paper, fontFamily: dStyles.mono,
          fontSize: 10, padding: '6px 9px', borderRadius: 6, lineHeight: 1.55,
          pointerEvents: 'none', minWidth: 160,
        }}>
          <div style={{ fontWeight: 700, color: D.honey }}>{data[hover.i].label}</div>
          {series.map(k => (
            <div key={k}>
              <span style={{ color: colors[k], fontWeight: 700 }}>■</span> {labels[k]}: {fmt(yMode === 'tokens' ? data[hover.i][k] : (yMode === 'cost' ? data[hover.i][k] * ({cacheRead:.0000003,cacheCreate:.000003,input:.000003,output:.000015}[k]) : data[hover.i][k] / series.reduce((a,kk)=>a+data[hover.i][kk],0)))}
            </div>
          ))}
          <div style={{ borderTop: `1px solid ${D.ink2}`, marginTop: 3, paddingTop: 3, color: D.honey }}>
            cost: ${data[hover.i].cost.toFixed(3)}
          </div>
          {events.filter(e => Math.abs(e.at - hover.i) <= 1).map((e, i) => (
            <div key={i} style={{ color: D.amber, marginTop: 2 }}>● {e.label}</div>
          ))}
        </div>
      )}

      {/* event hover popover */}
      {hoverEvt && (
        <div style={{
          position: 'absolute', right: 16, top: 16,
          background: D.paper, color: D.ink, fontFamily: dStyles.mono,
          fontSize: 11, padding: '6px 10px', borderRadius: 6,
          border: `1px solid ${D.ink}`, fontWeight: 700,
          boxShadow: `0 4px 12px ${D.paperShadow}`,
        }}>
          ● {hoverEvt.label}
        </div>
      )}
      </div>
    </D_Card>
  );
}

window.W1_CostRibbon = W1_CostRibbon;
window.W2_Health = W2_Health;
window.W3_Sankey = W3_CFD;
window.W3_CFD = W3_CFD;
window.W4_TokenBurn = W4_TokenBurn;
