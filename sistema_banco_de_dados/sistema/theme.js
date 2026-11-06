/* Somente a preferência de aparência é persistida; nenhum dado de clientes. */
(function () {
  'use strict';
  const root = document.documentElement;
  const key = 'wa_theme';
  function apply(value) {
    const dark = value === 'dark';
    root.dataset.theme = dark ? 'dark' : 'light';
    document.querySelectorAll('[data-theme-toggle]').forEach(button => {
      button.setAttribute('aria-pressed', String(dark));
      button.setAttribute('aria-label', dark ? 'Ativar modo claro' : 'Ativar modo escuro');
      const label = button.querySelector('[data-theme-label]');
      if (label) label.textContent = dark ? 'Modo claro' : 'Modo escuro';
    });
  }
  let saved = 'light';
  try { saved = localStorage.getItem(key); } catch (_) { /* Armazenamento bloqueado. */ }
  apply(saved);
  document.addEventListener('DOMContentLoaded', () => {
    apply(root.dataset.theme);
    document.querySelectorAll('[data-theme-toggle]').forEach(button => {
      button.addEventListener('click', () => {
        const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
        apply(next);
        try { localStorage.setItem(key, next); } catch (_) { /* Mantém a troca nesta página. */ }
      });
    });
  });
  window.addEventListener('storage', event => {
    if (event.key === key || event.key === null) apply(event.newValue);
  });
})();
