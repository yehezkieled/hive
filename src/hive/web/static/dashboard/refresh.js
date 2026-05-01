// Dashboard refresh: poll /api/dashboard/all every 30s and push the new
// payload into window.HIVE_DASH so React widgets re-read it.
//
// Auth: /api/dashboard/all is bearer-token gated. The token lands in
// sessionStorage on the user's first chat command from the landing page.
// If absent we silently skip — first paint still works because the server
// embedded HIVE_DASH inline.

(function () {
  const REFRESH_MS = 30000;

  function getToken() {
    try {
      return sessionStorage.getItem('hive_web_token');
    } catch (_) {
      return null;
    }
  }

  async function refreshOnce() {
    if (window.HIVE_AUTO_REFRESH === false) return;
    const token = getToken();
    if (!token) return;
    try {
      const resp = await fetch('/api/dashboard/all', {
        headers: { 'Authorization': 'Bearer ' + token },
      });
      if (!resp.ok) return;
      const data = await resp.json();
      window.HIVE_DASH = data;
      window.dispatchEvent(new Event('hive-data-updated'));
    } catch (_) {
      // Network/error — skip this tick; next interval tries again.
    }
  }

  setInterval(refreshOnce, REFRESH_MS);
})();
