(function () {
  'use strict';
  const panel = document.querySelector('[data-import-watch]');
  if (!panel) return;
  async function update() {
    try {
      const response = await fetch(panel.dataset.statusUrl, { credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(5000) });
      if (!response.ok || response.redirected) return;
      const status = await response.json();
      if (status.estado !== panel.dataset.state || !status.ativo) { window.location.reload(); return; }
      const progress = panel.querySelector('[data-import-progress]');
      if (progress) progress.textContent = status.progresso;
    } catch (error) { /* Uma falha de rede não envia nem confirma dados. */ }
    setTimeout(update, 1000);
  }
  setTimeout(update, 500);
})();
