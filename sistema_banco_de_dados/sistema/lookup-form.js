(function () {
  'use strict';
  const choices = document.querySelectorAll('input[name="tipo"]');
  if (!choices.length) return;
  function update() {
    const cpf = document.querySelector('input[name="tipo"]:checked').value === 'cpf';
    document.querySelectorAll('[data-phone-filter],[data-phone-guide]').forEach(element => { element.hidden = cpf; });
    document.querySelectorAll('[data-cpf-guide]').forEach(element => { element.hidden = !cpf; });
    const verification = document.querySelector('select[name="verificacao"]');
    if (verification) verification.disabled = cpf;
  }
  choices.forEach(choice => choice.addEventListener('change', update));
  update();
})();
