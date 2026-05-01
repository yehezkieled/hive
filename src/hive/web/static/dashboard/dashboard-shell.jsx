// Hive Dashboard tab — Paper Ops translation.
// Same warm palette as the Hive landing (A.2 / A3), but adapted for an
// observability/SRE surface: dense charts, anomaly-aware visuals, scrollable.
// Charts hand-rolled in SVG so the aesthetic stays cohesive (no Recharts
// defaults bleeding through).

const D = {
  paper:        '#faf7ed',
  paper2:       '#f2ecd8',
  paperSoft:    '#f6f1de',
  paperDeep:    '#ece4cc',
  paperShadow:  'rgba(60,45,25,0.08)',
  ink:          '#1f1812',
  ink2:         '#3d332a',
  ink3:         '#6f6356',
  ink4:         '#a79a89',
  ink5:         '#cabd9d',
  rule:         '#1f1812',
  ruleSoft:     '#d9cfb6',
  ruleFaint:    '#e8dfc8',
  accent:       '#c8382a',
  accentSoft:   '#f6dcd6',
  amber:        '#b7741a',
  amberSoft:    '#f4e4c8',
  honey:        '#e0a726',
  honeySoft:    '#f9ecc6',
  ochre:        '#8a6a1a',
  sage:         '#4d8a3a',
  sageSoft:     '#d7e6c5',
  vault:        '#1f6a5c',
  vaultSoft:    '#cfe3de',
  vaultSofter:  '#e5f0ec',
};
const dStyles = {
  mono: "'IBM Plex Mono', ui-monospace, Menlo, monospace",
  sans: "'Nunito Sans', system-ui, sans-serif",
  display: "'Nunito', system-ui, sans-serif",
};

if (typeof document !== 'undefined' && !document.getElementById('d-styles')) {
  const s = document.createElement('style');
  s.id = 'd-styles';
  s.textContent = `
    @keyframes d-pulse-edge { 0%{transform:scaleX(0);transform-origin:left} 50%{transform:scaleX(1);transform-origin:left} 51%{transform:scaleX(1);transform-origin:right} 100%{transform:scaleX(0);transform-origin:right} }
    @keyframes d-spin { to { transform: rotate(360deg); } }
    @keyframes d-blink { 0%,100%{opacity:.85} 50%{opacity:.25} }
    @keyframes d-shimmer { 0%{background-position:-300px 0} 100%{background-position:300px 0} }
    @keyframes d-bob { 0%,100%{transform:translateY(0)} 50%{transform:translateY(-2px)} }
    @keyframes d-hum { 0%,100%{transform:translate(0,0)} 25%{transform:translate(.5px,-.5px)} 50%{transform:translate(-.5px,.5px)} 75%{transform:translate(.5px,.5px)} }

    .d-card{transition:box-shadow .2s, border-color .2s;}
    .d-card.refreshing::before{
      content:''; position:absolute; left:0; right:0; top:-1px; height:2px;
      background: linear-gradient(90deg, transparent, ${D.honey}, transparent);
      animation: d-pulse-edge 1.4s ease-in-out infinite;
    }
    .d-btn{transition:transform .12s, background .15s, border-color .15s, color .15s;}
    .d-btn:hover{transform:translateY(-1px);}
    .d-btn:active{transform:translateY(0);}
    .d-tab{transition:background .15s, color .15s;}
    .d-chip{transition:background .15s, color .15s, border-color .15s;}
    .d-chip:hover{background:${D.paperSoft};}
    .d-chip.on{background:${D.ink}; color:${D.paper}; border-color:${D.ink};}
    .d-row{transition:background .12s;}
    .d-row:hover{background:${D.paperSoft};}
    .d-bubble{transition:transform .15s, filter .15s;}
    .d-bubble:hover{transform:scale(1.08);}
    .d-feed::-webkit-scrollbar{width:8px;}
    .d-feed::-webkit-scrollbar-track{background:${D.paperSoft};}
    .d-feed::-webkit-scrollbar-thumb{background:${D.ruleSoft}; border-radius:4px;}
    .d-feed::-webkit-scrollbar-thumb:hover{background:${D.ink4};}

    .d-skeleton{
      background: linear-gradient(90deg, ${D.paperSoft} 0%, ${D.paperDeep} 50%, ${D.paperSoft} 100%);
      background-size: 600px 100%;
      animation: d-shimmer 1.6s linear infinite;
      border-radius: 6px;
    }
  `;
  document.head.appendChild(s);
}

// ─── Re-use atoms from direction-a3 vocabulary ─────────────

function D_Bee({ size = 30 }) {
  return (
    <svg viewBox="0 0 64 64" width={size} height={size} style={{ overflow: 'visible' }}>
      <g style={{ transformOrigin: '32px 22px', animation: 'd-hum 220ms linear infinite' }}>
        <ellipse cx="22" cy="21" rx="9" ry="5.5" fill="#fff" stroke={D.ink} strokeWidth="1.8" opacity="0.9"/>
        <ellipse cx="42" cy="21" rx="9" ry="5.5" fill="#fff" stroke={D.ink} strokeWidth="1.8" opacity="0.9"/>
      </g>
      <ellipse cx="32" cy="38" rx="17" ry="15" fill={D.honey} stroke={D.ink} strokeWidth="2"/>
      <path d="M19 34 Q32 30.5 45 34" stroke={D.ink} strokeWidth="2.4" fill="none" strokeLinecap="round"/>
      <path d="M20 42 Q32 45.5 44 42" stroke={D.ink} strokeWidth="2.4" fill="none" strokeLinecap="round"/>
    </svg>
  );
}

function D_Hex({ size = 12, fill, stroke }) {
  return (
    <svg viewBox="0 0 20 20" width={size} height={size}>
      <path d="M10 1.5 L17.5 5.75 L17.5 14.25 L10 18.5 L2.5 14.25 L2.5 5.75 Z"
        fill={fill || D.honey} stroke={stroke || D.ink} strokeWidth="1.4" strokeLinejoin="round"/>
    </svg>
  );
}

function D_StateDot({ state, size = 8 }) {
  const map = { ok: D.sage, degraded: D.amber, failed: D.accent, gap: D.ink5, idle: D.ink4 };
  const c = map[state] || D.ink4;
  return <span style={{ width: size, height: size, borderRadius: '50%', background: c, display: 'inline-block', flexShrink: 0 }} />;
}

function D_PriorityPill({ p, size = 'sm' }) {
  const map = {
    P0: { bg: D.accent, fg: '#fff' }, P1: { bg: D.amber, fg: '#fff' },
    P2: { bg: D.honey, fg: D.ink }, P3: { bg: D.ink3, fg: '#fff' }, P4: { bg: D.ink4, fg: '#fff' },
  };
  const c = map[p] || map.P4;
  return (
    <span style={{
      fontFamily: dStyles.mono, fontSize: size === 'sm' ? 10 : 11, fontWeight: 700,
      color: c.fg, background: c.bg,
      padding: size === 'sm' ? '1.5px 6px' : '2px 7px', borderRadius: 4,
      letterSpacing: 0.3, display: 'inline-block', lineHeight: 1.3,
    }}>{p}</span>
  );
}

function D_NSPill({ ns }) {
  const map = {
    entity:  { bg: D.honeySoft,  fg: '#7a5a10', border: D.honey },
    task:    { bg: D.sageSoft,   fg: '#2d5a23', border: D.sage },
    command: { bg: D.paperSoft,  fg: D.ink2,    border: D.ruleSoft },
    vault:   { bg: D.vaultSofter,fg: D.vault,   border: D.vault + '55' },
    mcp:     { bg: D.accentSoft, fg: D.accent,  border: D.accent + '55' },
  };
  const c = map[ns] || map.command;
  return (
    <span style={{
      display: 'inline-block', padding: '1px 8px', borderRadius: 4,
      fontFamily: dStyles.mono, fontSize: 10, fontWeight: 700,
      background: c.bg, color: c.fg, border: `1px solid ${c.border}`,
      letterSpacing: 0.2, minWidth: 56, textAlign: 'center',
    }}>{ns}</span>
  );
}

// ─── Card frame ────────────────────────────────────────────

function D_Card({ title, sub, right, children, refreshing, style }) {
  return (
    <div className={`d-card${refreshing ? ' refreshing' : ''}`} style={{
      background: D.paper, border: `1px solid ${D.ruleSoft}`, borderRadius: 14,
      boxShadow: `0 1px 0 ${D.ruleFaint}`,
      position: 'relative', overflow: 'hidden',
      display: 'flex', flexDirection: 'column',
      ...style,
    }}>
      {(title || right) && (
        <div style={{
          padding: '12px 16px 10px', display: 'flex', alignItems: 'baseline', gap: 10,
          borderBottom: `1px dashed ${D.ruleSoft}`,
        }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0, flex: 1 }}>
            {title && (
              <h3 style={{
                margin: 0, fontFamily: dStyles.display, fontSize: 14, fontWeight: 800,
                color: D.ink, letterSpacing: -0.1,
              }}>{title}</h3>
            )}
            {sub && (
              <span style={{ fontSize: 11, color: D.ink3, fontFamily: dStyles.mono, fontWeight: 600 }}>{sub}</span>
            )}
          </div>
          {right}
        </div>
      )}
      <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
        {children}
      </div>
    </div>
  );
}

// ─── Top bar (re-used from A3, "Dashboard" tab active) ─────

function D_TopBar() {
  return (
    <div style={{
      height: 52, borderBottom: `1.5px solid ${D.rule}`, background: D.paper,
      display: 'flex', alignItems: 'stretch', fontFamily: dStyles.sans,
      fontSize: 13, color: D.ink, position: 'relative', zIndex: 3, flexShrink: 0,
    }}>
      <div style={{
        width: 220, borderRight: `1px solid ${D.ruleSoft}`,
        display: 'flex', alignItems: 'center', padding: '0 18px', gap: 11,
      }}>
        <div style={{ animation: 'd-bob 4.5s ease-in-out infinite', display: 'grid', placeItems: 'center' }}>
          <D_Bee size={30} />
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', lineHeight: 1 }}>
          <span style={{ fontFamily: dStyles.display, fontWeight: 800, letterSpacing: -0.4, fontSize: 17 }}>hive</span>
          <span style={{ fontFamily: dStyles.mono, fontSize: 9, color: D.ink3, letterSpacing: 1, marginTop: 3 }}>v0.4 · personal</span>
        </div>
      </div>
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', padding: '0 8px', gap: 2 }}>
        {[
          { label: 'Hive', href: 'Hive Landing.html' },
          { label: 'Projects', href: 'Hive Projects.html' },
          { label: 'Dashboard', active: true },
          { label: 'Knowledge' },
        ].map(t => (
          <a key={t.label} href={t.href || '#'} className="d-tab" style={{
            padding: '7px 14px', borderRadius: 8,
            background: t.active ? D.ink : 'transparent',
            color: t.active ? D.paper : D.ink2,
            fontWeight: t.active ? 700 : 600, fontSize: 13,
            cursor: 'pointer', position: 'relative', textDecoration: 'none',
          }}>
            {t.label}
            {t.active && (
              <span style={{
                position: 'absolute', left: 14, right: 14, bottom: -3, height: 2,
                background: D.accent, borderRadius: 2,
              }} />
            )}
          </a>
        ))}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '0 16px', borderLeft: `1px solid ${D.ruleSoft}` }}>
        <span style={{
          display: 'inline-flex', alignItems: 'center', gap: 7,
          padding: '4px 11px', background: D.sageSoft,
          border: `1px solid ${D.sage}33`, borderRadius: 999,
          fontSize: 11, fontWeight: 700, color: '#2d5a23',
        }}>
          <D_StateDot state="ok" size={7} /> all systems ok
        </span>
        <button className="d-btn" style={{
          padding: '6px 12px', borderRadius: 8, background: D.paper, color: D.ink2,
          border: `1px solid ${D.ruleSoft}`,
          fontFamily: dStyles.mono, fontSize: 11, fontWeight: 600, cursor: 'pointer',
          display: 'inline-flex', alignItems: 'center', gap: 6,
        }}>
          <span style={{ color: D.ink3 }}>⌃</span> terminal
        </button>
      </div>
    </div>
  );
}

function D_TerminalBar() {
  return (
    <div style={{
      height: 30, borderTop: `1.5px solid ${D.rule}`, background: D.ink,
      color: D.paper, fontFamily: dStyles.mono, fontSize: 11,
      display: 'flex', alignItems: 'center', padding: '0 16px', gap: 12, flexShrink: 0,
    }}>
      <span style={{ fontWeight: 700 }}>▲ terminal</span>
      <span style={{
        padding: '1px 7px', background: D.sage, color: '#fff',
        borderRadius: 4, fontWeight: 700, fontSize: 10,
      }}>unlocked</span>
      <span style={{ color: 'rgba(250,247,237,0.5)' }}>sudo available · expires in 28m</span>
      <span style={{ marginLeft: 'auto', color: D.honey, fontWeight: 700 }}>~/hive $</span>
    </div>
  );
}

// ─── Page header strip ─────────────────────────────────────

function D_PageHeader({ range, setRange, autoRefresh, setAutoRefresh, lastUpdated }) {
  const ranges = ['1h', '24h', '7d', '30d'];
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 16, marginBottom: 16,
      padding: '12px 16px', background: D.paper,
      border: `1px solid ${D.ruleSoft}`, borderRadius: 12,
      boxShadow: `0 1px 0 ${D.ruleFaint}`,
    }}>
      <div>
        <h1 style={{
          margin: 0, fontFamily: dStyles.display, fontSize: 20, fontWeight: 800,
          letterSpacing: -0.5, color: D.ink,
        }}>System dashboard</h1>
        <p style={{ margin: '2px 0 0', fontSize: 11.5, color: D.ink3, fontFamily: dStyles.mono, fontWeight: 600 }}>
          how is the hive itself behaving — health · cost · throughput
        </p>
      </div>

      <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{ display: 'flex', gap: 0, padding: 3, background: D.paperSoft, border: `1px solid ${D.ruleSoft}`, borderRadius: 9 }}>
          {ranges.map(r => (
            <button key={r} className="d-btn" onClick={() => setRange(r)} style={{
              padding: '4px 11px', borderRadius: 6,
              background: range === r ? D.ink : 'transparent',
              color: range === r ? D.paper : D.ink2,
              border: 'none', fontFamily: dStyles.mono, fontSize: 11, fontWeight: 700,
              cursor: 'pointer',
            }}>{r}</button>
          ))}
        </div>

        <span style={{
          fontFamily: dStyles.mono, fontSize: 11, color: D.ink3, fontWeight: 600,
          display: 'inline-flex', alignItems: 'center', gap: 6,
        }}>
          <span style={{ width: 6, height: 6, borderRadius: '50%', background: D.sage, animation: 'd-blink 1.6s infinite' }} />
          updated {lastUpdated} ago
        </span>

        <button className="d-btn" style={{
          width: 32, height: 32, borderRadius: 8, background: D.paper,
          border: `1px solid ${D.ruleSoft}`, cursor: 'pointer', display: 'grid', placeItems: 'center', padding: 0,
        }} title="Refresh">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={D.ink2} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 4v5h-5"/></svg>
        </button>

        <button className="d-btn" onClick={() => setAutoRefresh(!autoRefresh)} style={{
          padding: '5px 11px', borderRadius: 8,
          background: autoRefresh ? D.honeySoft : D.paper,
          border: `1px solid ${autoRefresh ? D.honey : D.ruleSoft}`,
          fontSize: 11, fontWeight: 700, fontFamily: dStyles.sans,
          color: autoRefresh ? D.ink : D.ink3,
          cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 6,
        }}>
          <span style={{
            width: 22, height: 12, background: autoRefresh ? D.honey : D.ruleSoft,
            borderRadius: 999, position: 'relative', display: 'inline-block',
          }}>
            <span style={{
              position: 'absolute', top: 1, left: autoRefresh ? 11 : 1, width: 10, height: 10,
              borderRadius: '50%', background: D.paper, transition: 'left .15s',
            }} />
          </span>
          auto · 30s
        </button>
      </div>
    </div>
  );
}

// ─── Backdrop honeycomb (very faint) ───────────────────────

function D_Backdrop() {
  const hex = encodeURIComponent(`
    <svg xmlns='http://www.w3.org/2000/svg' width='64' height='74' viewBox='0 0 64 74'>
      <g fill='none' stroke='${D.ink}' stroke-opacity='0.04' stroke-width='1.2' stroke-linejoin='round'>
        <path d='M16 1 L48 1 L64 18.5 L64 55.5 L48 73 L16 73 L0 55.5 L0 18.5 Z'/>
      </g>
    </svg>
  `);
  return (
    <div style={{
      position: 'absolute', inset: 0, pointerEvents: 'none',
      backgroundImage: `url("data:image/svg+xml;utf8,${hex}")`,
      backgroundSize: '64px 74px',
      zIndex: 0,
    }} />
  );
}

Object.assign(window, {
  D, dStyles, D_Bee, D_Hex, D_StateDot, D_PriorityPill, D_NSPill,
  D_Card, D_TopBar, D_TerminalBar, D_PageHeader, D_Backdrop,
});
