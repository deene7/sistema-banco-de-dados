(function () {
  'use strict';
  const panel=document.querySelector('[data-lookup-list]');
  if(!panel || !panel.querySelector('[data-lookup-id]'))return;
  const labels={mapeamento:'Conferir CPF',fila:'Na fila',processando:'Processando',concluida:'Concluída',erro:'Com erro',interrompida:'Interrompida',expirada:'Arquivos expirados'};
  let pending=false,stopped=false,timer,delay=3000;
  async function update(){
    if(pending || stopped || document.hidden)return;
    pending=true;
    try{
      const response=await fetch(panel.dataset.statusUrl,{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(8000)});
      if(response.redirected || [401,403,404].includes(response.status)){stopped=true;return;}
      if(!response.ok)throw new Error('Status indisponível');
      const data=await response.json();
      data.jobs.forEach(job=>{
        if(!/^[a-f0-9]{32}$/.test(job.token))return;
        const row=panel.querySelector('[data-lookup-id="'+job.token+'"]');
        if(row)row.querySelector('[data-lookup-state]').textContent=labels[job.estado] || job.estado;
      });
      delay=data.jobs.some(job=>['fila','processando'].includes(job.estado)) ? 3000 : 15000;
    }catch(error){delay=Math.min(delay*2,30000);}
    finally{pending=false;if(!stopped && !document.hidden)timer=setTimeout(update,delay);}
  }
  document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(!document.hidden)update();});
  window.addEventListener('pagehide',()=>{stopped=true;clearTimeout(timer);});
  window.addEventListener('pageshow',event=>{if(event.persisted){stopped=false;update();}});
  timer=setTimeout(update,1000);
})();
