/* Máscaras locais. O servidor valida novamente todos os valores ao salvar. */
(function (root) {
  'use strict';
  const onlyDigits = value => String(value || '').replace(/[^0-9]/g, '');
  function normalized(value, kind, allowCountry) {
    let valueDigits = onlyDigits(value);
    if (kind === 'phone' && allowCountry && [12, 13].includes(valueDigits.length) && valueDigits.startsWith('55')) {
      valueDigits = valueDigits.slice(2);
    }
    return valueDigits.slice(0, 11);
  }
  function formatCPF(value) {
    const d = onlyDigits(value).slice(0, 11);
    return d.slice(0, 3) + (d.length > 3 ? '.' + d.slice(3, 6) : '') +
      (d.length > 6 ? '.' + d.slice(6, 9) : '') + (d.length > 9 ? '-' + d.slice(9, 11) : '');
  }
  function formatPhone(value) {
    const d = onlyDigits(value).slice(0, 11);
    if (!d) return '';
    if (d.length <= 2) return '(' + d;
    const subscriber = d.slice(2);
    const split = d.length === 11 ? 5 : 4;
    return '(' + d.slice(0, 2) + ') ' + subscriber.slice(0, split) +
      (subscriber.length > split ? '-' + subscriber.slice(split) : '');
  }
  function format(value, kind, country) {
    const d = normalized(value, kind, country);
    return kind === 'cpf' ? formatCPF(d) : formatPhone(d);
  }
  function positionForDigits(value, count) {
    if (count <= 0) return 0;
    let seen = 0;
    for (let i = 0; i < value.length; i++) {
      if (/[0-9]/.test(value[i]) && ++seen === count) return i + 1;
    }
    return value.length;
  }
  function formatFileSize(size) {
    if (size < 1024) return size + ' bytes';
    if (size < 1024 * 1024) return (size / 1024).toLocaleString('pt-BR', { maximumFractionDigits: 1 }) + ' KB';
    return (size / (1024 * 1024)).toLocaleString('pt-BR', { maximumFractionDigits: 2 }) + ' MB';
  }
  const api = { onlyDigits, normalized, formatCPF, formatPhone, format, positionForDigits, formatFileSize };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (!root.document) return;
  root.document.querySelectorAll('input[data-mask]').forEach(input => {
    const kind = input.dataset.mask;
    input.value = format(input.value, kind, true);
    input.addEventListener('beforeinput', event => {
      if (event.inputType === 'insertText' && event.data && /[0-9]/.test(event.data) &&
          onlyDigits(input.value).length >= 11 && input.selectionStart === input.selectionEnd) event.preventDefault();
    });
    input.addEventListener('input', () => {
      const before = input.value;
      const count = onlyDigits(before.slice(0, input.selectionStart || 0)).length;
      input.value = format(before, kind, false);
      const caret = positionForDigits(input.value, count);
      input.setSelectionRange(caret, caret);
    });
    input.addEventListener('paste', event => {
      if (!event.clipboardData) return;
      event.preventDefault();
      const proposed = input.value.slice(0, input.selectionStart || 0) +
        event.clipboardData.getData('text') + input.value.slice(input.selectionEnd || 0);
      input.value = format(proposed, kind, true);
      input.setSelectionRange(input.value.length, input.value.length);
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });
    // Backspace/Delete sobre um separador remove o dígito vizinho, sem prender o cursor.
    input.addEventListener('keydown', event => {
      const start = input.selectionStart;
      if (start !== input.selectionEnd || !['Backspace', 'Delete'].includes(event.key)) return;
      const position = event.key === 'Backspace' ? start - 1 : start;
      if (position < 0 || position >= input.value.length || /[0-9]/.test(input.value[position])) return;
      const beforeDigits = onlyDigits(input.value.slice(0, start)).length;
      const d = onlyDigits(input.value);
      const remove = event.key === 'Backspace' ? beforeDigits - 1 : beforeDigits;
      if (remove < 0 || remove >= d.length) return;
      event.preventDefault();
      input.value = format(d.slice(0, remove) + d.slice(remove + 1), kind, false);
      const caret = positionForDigits(input.value, remove);
      input.setSelectionRange(caret, caret);
    });
    input.addEventListener('blur', () => { input.value = format(input.value, kind, false); });
  });
  root.document.querySelectorAll('[data-file-picker]').forEach(picker => {
    const input = picker.querySelector('input[type="file"]');
    const title = picker.querySelector('[data-file-title]');
    const description = picker.querySelector('[data-file-description]');
    const button = picker.querySelector('[data-file-button]');
    const error = picker.querySelector('[data-file-error]');
    function update() {
      const file = input.files && input.files[0];
      let message = '';
      if (file && !/\.(csv|xlsx)$/i.test(file.name)) message = 'Escolha um arquivo CSV ou Excel .xlsx.';
      if (file && file.size > 50 * 1024 * 1024) message = 'O arquivo excede 50 MB. Divida em arquivos menores.';
      if (file && file.size === 0) message = 'O arquivo está vazio. Escolha outro arquivo.';
      input.setCustomValidity(message);
      error.textContent = message;
      picker.classList.toggle('has-file', !!file);
      picker.classList.toggle('file-invalid', !!message);
      title.textContent = file ? file.name : 'Selecione ou arraste seu arquivo';
      description.textContent = file ? formatFileSize(file.size) + ' · ' + (message ? 'Confira o arquivo' : 'Pronto para enviar') : 'CSV ou Excel .xlsx · Até 50 MB';
      button.textContent = file ? 'Trocar arquivo' : 'Escolher arquivo';
    }
    input.addEventListener('change', update);
    ['dragenter', 'dragover'].forEach(name => picker.addEventListener(name, event => {
      event.preventDefault(); picker.classList.add('drag-over');
    }));
    picker.addEventListener('dragleave', event => {
      if (!picker.contains(event.relatedTarget)) picker.classList.remove('drag-over');
    });
    picker.addEventListener('drop', event => {
      event.preventDefault(); picker.classList.remove('drag-over');
      if (!event.dataTransfer || event.dataTransfer.files.length !== 1) {
        input.setCustomValidity('Selecione somente um arquivo por lote.');
        error.textContent = 'Selecione somente um arquivo por lote.';
        return;
      }
      input.files = event.dataTransfer.files;
      update();
    });
    update();
  });
})(typeof window === 'undefined' ? globalThis : window);
