(function () {
  'use strict';
  const panel = document.querySelector('[data-summary-url]');
  if (!panel) return;
  let timer, stopped = false, pending = false, delay = 1000;
  async function update() {
    if (stopped || pending || document.hidden) return;
    pending = true;
    try {
      const response = await fetch(panel.dataset.summaryUrl, {
        credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(5000)
      });
      if (response.redirected || [401,403,404].includes(response.status)) { stopped = true; return; }
      if (!response.ok) throw new Error('Resumo indisponível');
      const data = await response.json();
      if (data.stats) {
        panel.querySelectorAll('[data-summary-key]').forEach(element => {
          const value = data.stats[element.dataset.summaryKey];
          if (Number.isFinite(value)) element.textContent = value.toLocaleString('pt-BR');
        });
        panel.removeAttribute('aria-busy');
        stopped = true;
      }
    } catch (error) { /* Mantém a navegação disponível durante falhas temporárias. */ }
    finally {
      pending = false;
      if (!stopped && !document.hidden) {
        timer = setTimeout(update, delay);
        delay = Math.min(delay * 2, 15000);
      }
    }
  }
  document.addEventListener('visibilitychange', () => {
    clearTimeout(timer);
    if (!document.hidden) update();
  });
  window.addEventListener('pagehide', () => { stopped = true; clearTimeout(timer); });
  window.addEventListener('pageshow', event => { if (event.persisted && panel.hasAttribute('aria-busy')) { stopped = false; update(); } });
  update();
})();
