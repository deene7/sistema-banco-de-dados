/* Guarda somente tamanho da página e ordenação, sem dados de clientes ou buscas. */
(function () {
  'use strict';
  document.addEventListener('submit', function (event) {
    const form = event.target;
    if (!form.hasAttribute('data-display-preferences')) return;
    queueMicrotask(function () {
      if (event.defaultPrevented) return;
      const values = new FormData(form);
      const preferences = [
        ['por_pagina', 'wa_page_size', ['25','50','100','200']],
        ['ordem', 'wa_sort', ['nome','recentes','antigos']]
      ];
      preferences.forEach(([field, cookie, allowed]) => {
        const value = values.get(field);
        if (allowed.includes(value)) {
          document.cookie = cookie + '=' + value + '; Path=/; Max-Age=31536000; SameSite=Strict';
        }
      });
    });
  });
})();
