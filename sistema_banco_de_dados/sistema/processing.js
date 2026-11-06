(function () {
  'use strict';
  const panel = document.querySelector('[data-processing-center]');
  if (!panel || !panel.querySelector('[data-job-id]')) return;
  const labels = {fila:'Na fila',preparando:'Analisando arquivo',importando:'Importando',revisao:'Aguardando revisão',concluida:'Concluída',erro:'Com erro',interrompido:'Interrompido',mapeamento:'Conferir colunas'};
  let pending = false, stopped = false, timer, delay = 3000;
  async function update() {
    if (pending || stopped || document.hidden) return;
    pending = true;
    try {
      const response = await fetch(panel.dataset.statusUrl,{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(8000)});
      if (response.redirected || [401,403,404].includes(response.status)) { stopped=true; return; }
      if (!response.ok) throw new Error('Status indisponível');
      const data = await response.json();
      const present = new Set();
      data.jobs.forEach(job => {
        if (!Number.isSafeInteger(job.id)) return;
        present.add(String(job.id));
        const row = panel.querySelector('[data-job-id="'+job.id+'"]');
        if (!row) return;
        row.querySelector('[data-job-state]').textContent = (labels[job.situacao] || job.situacao)+(job.posicao ? ' · posição '+job.posicao : '');
        row.querySelector('[data-job-progress]').textContent = job.progresso+(job.meta ? ' de '+job.meta : '')+(job.estado==='preparando' ? ' linhas lidas' : ' clientes processados');
        const progress = row.querySelector('[data-job-percent]');
        progress.hidden = !job.ativo || job.situacao==='fila';
        if (job.percentual === null) progress.removeAttribute('value'); else progress.value = job.percentual;
        row.querySelector('[data-job-time]').textContent = job.ativo && job.situacao!=='fila' ? 'Em execução há '+job.decorridos+' s' : '';
        row.querySelector('[data-job-resume]').hidden = !job.retomar;
        row.querySelector('[data-job-review]').hidden = job.estado!=='revisao';
      });
      panel.querySelectorAll('[data-job-id]').forEach(row => {
        if (!present.has(row.dataset.jobId)) {
          row.querySelector('[data-job-state]').textContent='Lote removido da lista ativa';
          row.querySelector('[data-job-resume]').hidden=true;
          row.querySelector('[data-job-review]').hidden=true;
          row.querySelector('[data-job-percent]').hidden=true;
        }
      });
      document.querySelectorAll('[data-processing-count]').forEach(element => {element.textContent=(data.totals[element.dataset.processingCount] || 0).toLocaleString('pt-BR');});
      panel.querySelector('[data-processing-message]').textContent='Atualizado às '+new Date().toLocaleTimeString('pt-BR')+'. Novos lotes aparecem ao atualizar a lista.';
      delay=data.jobs.some(job=>job.ativo) ? 3000 : 15000;
    } catch (error) {
      delay=Math.min(delay*2,30000);
      panel.querySelector('[data-processing-message]').textContent='Sem atualização no momento. Tentaremos novamente automaticamente.';
    } finally {
      pending=false;
      if (!stopped && !document.hidden) timer=setTimeout(update,delay);
    }
  }
  document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(!document.hidden)update();});
  window.addEventListener('pagehide',()=>{stopped=true;clearTimeout(timer);});
  window.addEventListener('pageshow',event=>{if(event.persisted){stopped=false;update();}});
  timer=setTimeout(update,1000);
})();
