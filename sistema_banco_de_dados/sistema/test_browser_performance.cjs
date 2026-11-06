const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const settle = () => new Promise(resolve => setImmediate(resolve));
function watcher(file, responses) {
  const events = {}, windowEvents = {}, timers = new Map();
  let nextTimer = 0, requests = 0, reloads = 0;
  const progress = {textContent: ''};
  const values = ['total','sem_nome','sem_telefone','sem_email'].map(key => ({dataset:{summaryKey:key},textContent:''}));
  const panel = {
    dataset: {statusUrl:'/status', state:'importando', summaryUrl:'/inicio/resumo'},
    querySelector: () => progress, querySelectorAll: () => values,
    removeAttribute() {this.busy = false;}, hasAttribute() {return this.busy;}, busy:true
  };
  const document = {hidden:false, querySelector:()=>panel, addEventListener:(type,fn)=>{events[type]=fn;}};
  const context = {
    document, AbortSignal, console,
    window: {location:{reload:()=>reloads++}, addEventListener:(type,fn)=>{windowEvents[type]=fn;}},
    fetch: async () => {
      requests++;
      const item = responses.shift();
      if (item instanceof Error) throw item;
      assert.ok(item, 'Consulta extra inesperada');
      return {ok:true,status:200,redirected:false,...item,json:async()=>item.data};
    },
    setTimeout: (fn,delay) => {const id=++nextTimer;timers.set(id,{fn,delay});return id;},
    clearTimeout: id => timers.delete(id)
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../static',file),'utf8'),context);
  return {document,events,windowEvents,timers,progress,values,panel,
    get requests(){return requests;},get reloads(){return reloads;},
    async tick(){const [id,timer]=timers.entries().next().value;timers.delete(id);await timer.fn();await settle();}};
}
(async () => {
  const h = watcher('import-progress.js', [
    {data:{estado:'importando',ativo:true,progresso:12}},
    new Error('Rede indisponível'),
    {data:{estado:'concluida',ativo:false,progresso:100}}
  ]);
  await h.tick();
  assert.equal(h.progress.textContent,12);
  h.document.hidden=true;h.events.visibilitychange();
  assert.equal(h.timers.size,0);
  h.document.hidden=false;h.events.visibilitychange();await settle();
  assert.equal(h.requests,2);
  assert.equal(h.timers.size,1);
  assert.ok([...h.timers.values()][0].delay>=2000);
  await h.tick();assert.equal(h.reloads,1);assert.equal(h.timers.size,0);
  const denied = watcher('import-progress.js',[{redirected:true}]);
  await denied.tick();assert.equal(denied.timers.size,0);
  const home = watcher('home-summary.js',[
    {status:202,data:{stats:null}},
    {data:{stats:{total:1234,sem_nome:2,sem_telefone:3,sem_email:4}}}
  ]);
  await settle();await home.tick();
  assert.equal(home.values[0].textContent,(1234).toLocaleString('pt-BR'));
  assert.equal(home.panel.busy,false);assert.equal(home.timers.size,0);
  const cookies = [], handlers = {};
  const doc = {addEventListener:(name,fn)=>{handlers[name]=fn;},set cookie(value){cookies.push(value);}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../static/display-preferences.js'),'utf8'),{
    document:doc,queueMicrotask,FormData:class{constructor(form){return form.values;}}
  });
  const form={hasAttribute:()=>true, values:new Map([['por_pagina','100'],['ordem','recentes'],['q','dado privado']])};
  handlers.submit({target:form,defaultPrevented:false});await settle();
  assert.equal(cookies.length,2);assert.ok(cookies[0].startsWith('wa_page_size=100;'));
  assert.ok(!cookies.join('').includes('privado'));
  form.values=new Map([['por_pagina','999'],['ordem','invalida']]);
  handlers.submit({target:form,defaultPrevented:false});await settle();assert.equal(cookies.length,2);
  console.log('OK: pausa em abas ocultas, retomada, falhas de rede, resumo assíncrono e preferências sem dados de clientes.');
})().catch(error=>{console.error(error);process.exitCode=1;});
