(function () {
  'use strict';
  const panel=document.querySelector('[data-quality-panel]');
  if (!panel) return;
  let pending=false,stopped=false,timer,delay=1000;
  function render(stats) {
    panel.querySelectorAll('[data-quality-key]').forEach(element=>{
      const value=Number(stats[element.dataset.qualityKey]);
      if(Number.isFinite(value))element.textContent=value.toLocaleString('pt-BR');
    });
    panel.querySelectorAll('[data-quality-percent]').forEach(element=>{element.value=stats[element.dataset.qualityPercent] || 0;});
    const body=panel.querySelector('[data-quality-categories]');
    const fragment=document.createDocumentFragment();
    stats.categorias.forEach(category=>{
      const row=document.createElement('tr');
      [category.nome,category.clientes.toLocaleString('pt-BR'),category.verificados.toLocaleString('pt-BR'),category.cobertura.toLocaleString('pt-BR')+'%'].forEach(value=>{
        const cell=document.createElement('td');cell.textContent=value;row.appendChild(cell);
      });
      const cell=document.createElement('td'),link=document.createElement('a');
      link.className='text-link';link.textContent='Consultar →';
      link.href='/?'+new URLSearchParams({categoria:String(category.id)});cell.appendChild(link);row.appendChild(cell);fragment.appendChild(row);
    });
    if (!stats.categorias.length) {
      const row=document.createElement('tr'),cell=document.createElement('td');cell.colSpan=5;cell.textContent='Nenhuma categoria ativa.';cell.className='empty compact';row.appendChild(cell);fragment.appendChild(row);
    }
    body.replaceChildren(fragment);
    panel.querySelector('[data-quality-updated]').textContent='Atualizado em '+stats.atualizado_em;
    panel.removeAttribute('aria-busy');
  }
  async function update() {
    if(pending || stopped || document.hidden)return;
    pending=true;
    try {
      const response=await fetch(panel.dataset.statusUrl,{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(8000)});
      if(response.redirected || [401,403,404].includes(response.status)){stopped=true;return;}
      if(!response.ok)throw new Error('Resumo indisponível');
      const data=await response.json();
      if(data.stats)render(data.stats);
      delay=data.stats && !data.refreshing ? 120000 : Math.min(delay*2,5000);
    } catch(error) {
      delay=Math.min(delay*2,30000);
      panel.querySelector('[data-quality-updated]').textContent='Indicadores temporariamente indisponíveis. A atualização será tentada novamente.';
    } finally {
      pending=false;
      if(!stopped && !document.hidden)timer=setTimeout(update,delay);
    }
  }
  document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(!document.hidden)update();});
  window.addEventListener('pagehide',()=>{stopped=true;clearTimeout(timer);});
  window.addEventListener('pageshow',event=>{if(event.persisted){stopped=false;update();}});
  update();
})();
