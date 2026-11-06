(function () {
  'use strict';
  const panel = document.querySelector('[data-import-watch]');
  if (!panel) return;
  let timer, stopped = false, pending = false, delay = 1000, previous;
  async function update() {
    if (stopped || pending || document.hidden) return;
    pending = true;
    try {
      const response = await fetch(panel.dataset.statusUrl, { credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(5000) });
      if (response.redirected || [401,403,404].includes(response.status)) { stopped = true; return; }
      if (!response.ok) throw new Error('Atualização indisponível');
      const status = await response.json();
      if (status.estado !== panel.dataset.state || !status.ativo) { stopped = true; window.location.reload(); return; }
      const progress = panel.querySelector('[data-import-progress]');
      if (progress) progress.textContent = status.progresso;
      const phase = panel.querySelector('[data-import-phase]');
      if (phase && status.fase) phase.textContent = status.fase;
      delay = status.progresso === previous ? Math.min(delay * 1.5, 5000) : 1000;
      previous = status.progresso;
    } catch (error) { delay = Math.min(delay * 2, 15000); }
    finally {
      pending = false;
      if (!stopped && !document.hidden) timer = setTimeout(update, delay);
    }
  }
  document.addEventListener('visibilitychange', () => {
    clearTimeout(timer);
    if (!document.hidden) update();
  });
  window.addEventListener('pagehide', () => { stopped = true; clearTimeout(timer); });
  window.addEventListener('pageshow', event => { if (event.persisted) { stopped = false; update(); } });
  timer = setTimeout(update, 500);
})();
