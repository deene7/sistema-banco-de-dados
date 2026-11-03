(function () {
  'use strict';
  let busy = false;
  const overlay = document.getElementById('page-processing');
  function show(title) {
    if (!overlay) return;
    overlay.querySelector('[data-processing-title]').textContent = title;
    overlay.hidden = false;
    document.querySelector('main').setAttribute('aria-busy', 'true');
  }
  function titleFor(path, method) {
    if (method === 'get') return 'Carregando resultados…';
    if (path.endsWith('/excluir-definitivamente')) return 'Excluindo permanentemente…';
    if (path.endsWith('/excluir')) return 'Movendo para a lixeira…';
    if (path.endsWith('/restaurar')) return 'Restaurando registros…';
    if (path.endsWith('/enviar')) return 'Enviando e preparando o arquivo…';
    if (/\/(mapear|revisar-novamente)$/.test(path)) return 'Analisando o arquivo…';
    if (/\/(confirmar|retomar)$/.test(path)) return 'Iniciando o processamento…';
    if (path.endsWith('/login')) return 'Entrando no sistema…';
    if (path.endsWith('/sair')) return 'Saindo da conta…';
    return 'Salvando alterações…';
  }
  document.addEventListener('submit', function (event) {
    const form = event.target;
    if (event.defaultPrevented || form.target === '_blank' || form.hasAttribute('data-no-loading')) return;
    const action = new URL(form.action || window.location.href, window.location.href);
    if (action.origin !== window.location.origin || /\.csv$/i.test(action.pathname)) return;
    if (busy) { event.preventDefault(); return; }
    const method = (form.method || 'get').toLowerCase();
    if (method !== 'post') return;
    busy = true;
    // Aguarda outros validadores do mesmo evento, sem reenviar o formulário.
    queueMicrotask(function () {
      if (event.defaultPrevented) { busy = false; return; }
      form.dataset.processing = '1';
      form.setAttribute('aria-busy', 'true');
      const title = titleFor(action.pathname, method);
      show(title);
      const button = event.submitter || form.querySelector('button[type="submit"],button:not([type])');
      if (button && button.tagName === 'BUTTON') {
        button.dataset.processingLabel = button.textContent;
        button.textContent = title;
        button.classList.add('is-processing');
        // A captura dos valores do envio acontece antes de desabilitar o botão.
        setTimeout(function () { button.disabled = true; }, 0);
      }
    });
  });
  document.addEventListener('click', function (event) {
    // Navegação comum não abre aviso. Só protege o envio POST já em andamento.
    if (!busy || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest('a[href]');
    if (!link || link.target === '_blank' || link.hasAttribute('download') || link.hasAttribute('data-no-loading')) return;
    const url = new URL(link.href, window.location.href);
    if (url.origin !== window.location.origin || url.hash || /\.(csv|xlsx|pdf|png|svg)$/i.test(url.pathname)) return;
    event.preventDefault();
  });
  window.addEventListener('pageshow', function () {
    busy = false;
    if (overlay) overlay.hidden = true;
    const main = document.querySelector('main');
    if (main) main.removeAttribute('aria-busy');
    document.querySelectorAll('form[data-processing="1"]').forEach(function (form) {
      delete form.dataset.processing; form.removeAttribute('aria-busy');
      form.querySelectorAll('button.is-processing').forEach(function (button) {
        button.disabled = false; button.classList.remove('is-processing');
        button.textContent = button.dataset.processingLabel || 'Continuar';
        delete button.dataset.processingLabel;
      });
    });
  });
})();
