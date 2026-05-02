(function () {
// Top-level page composition + React mount.
// Loaded as the LAST external `<script type="text/babel">` so the previous
// three (shell, w1234, w5678) have finished assigning their exports onto
// `window` by the time this file evaluates.

const { D, dStyles, D_PageHeader } = window;

function DashboardPage() {
  const [range, setRange] = React.useState('24h');
  const [autoRefresh, setAutoRefresh] = React.useState(true);
  const [, forceTick] = React.useState(0);

  // Re-render when the polling loop reassigns window.HIVE_DASH.
  React.useEffect(() => {
    const handler = () => forceTick(t => t + 1);
    window.addEventListener('hive-data-updated', handler);
    return () => window.removeEventListener('hive-data-updated', handler);
  }, []);

  // Mirror autoRefresh into a global so refresh.js can read it.
  React.useEffect(() => {
    window.HIVE_AUTO_REFRESH = autoRefresh;
  }, [autoRefresh]);

  return (
    <div style={{
      background: D.paper, color: D.ink,
      fontFamily: dStyles.sans, position: 'relative',
      minHeight: '100%',
    }}>
      <div style={{ padding: '18px 24px 28px' }}>
        <D_PageHeader
          range={range} setRange={setRange}
          autoRefresh={autoRefresh} setAutoRefresh={setAutoRefresh}
          lastUpdated={window.HIVE_DASH.lastUpdated}
        />

        {/* Row 1: cost ribbon (60) + health (40) */}
        <div style={{ display: 'flex', gap: 14, marginBottom: 14 }}>
          <div style={{ flex: '1 1 60%', display: 'flex' }}>
            <window.W1_CostRibbon />
          </div>
          <div style={{ flex: '1 1 40%', display: 'flex' }}>
            <window.W2_Health />
          </div>
        </div>

        {/* Row 2: token burn */}
        <div style={{ marginBottom: 14 }}>
          <window.W4_TokenBurn range={range} />
        </div>

        {/* Row 3: bubble matrix + cache hit */}
        <div style={{
          display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)',
          gap: 14, marginBottom: 14, alignItems: 'stretch',
        }}>
          <div style={{ minWidth: 0 }}><window.W5_BubbleMatrix /></div>
          <div style={{ minWidth: 0 }}><window.W6_CacheHit /></div>
        </div>

        {/* Row 4: audit log */}
        <div style={{ marginBottom: 14 }}><window.W7_AuditLog /></div>

        {/* Row 5: failure scatter */}
        <div style={{ marginBottom: 14 }}><window.W8_Failure /></div>

        {/* Row 6: workload CFD */}
        <div style={{ marginBottom: 4 }}><window.W3_CFD /></div>
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<DashboardPage />);
})();
