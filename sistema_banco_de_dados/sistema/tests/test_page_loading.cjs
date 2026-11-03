const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/page-loading.js'), 'utf8');
function harness() {
  const events = {}, windowEvents = {}, microtasks = [], timers = [];
  const heading = {textContent: ''};
  const overlay = {hidden: true, querySelector: () => heading};
  const main = {setAttribute(){}, removeAttribute(){}};
  const forms = [];
  const document = {
    getElementById: () => overlay,
    querySelector: () => main,
    querySelectorAll: () => forms.filter(form => form.dataset.processing === '1'),
    addEventListener: (name, handler) => {events[name] = handler;}
  };
  const location = new URL('http://127.0.0.1:8080/importacoes/22/excluir');
  vm.runInNewContext(source, {document, window:{location,addEventListener:(name,handler)=>{windowEvents[name]=handler;}},
    URL, queueMicrotask:handler=>microtasks.push(handler), setTimeout:handler=>timers.push(handler)});
  function flush() {while(microtasks.length) microtasks.shift()(); while(timers.length) timers.shift()();}
  function form(action, method='post') {
    const classes = new Set();
    const button = {tagName:'BUTTON',textContent:'Confirmar',disabled:false,dataset:{},classList:{add:x=>classes.add(x),remove:x=>classes.delete(x)}};
    const result = {action,method,dataset:{},hasAttribute:()=>false,setAttribute(){},removeAttribute(){},
      querySelector:()=>button,querySelectorAll:()=>classes.has('is-processing')?[button]:[]};
    forms.push(result);return {result,button};
  }
  function submit(target, prevented=false) {
    const event = {target,defaultPrevented:prevented,preventDefault(){this.defaultPrevented=true;}};
    events.submit(event);return event;
  }
  return {events,windowEvents,overlay,heading,flush,form,submit};
}
{
  const h=harness(), f=h.form('http://127.0.0.1:8080/importacoes/22/excluir');
  h.submit(f.result);h.flush();
  assert.equal(h.overlay.hidden,false);
  assert.equal(h.heading.textContent,'Movendo para a lixeira…');
  assert.equal(f.button.disabled,true);
  assert.equal(h.submit(f.result).defaultPrevented,true);
  h.windowEvents.pageshow();
  assert.equal(h.overlay.hidden,true);
  assert.equal(f.button.disabled,false);
  assert.equal(f.button.textContent,'Confirmar');
}
{
  const h=harness(), f=h.form('http://127.0.0.1:8080/clientes/1/excluir');
  h.submit(f.result,true);h.flush();assert.equal(h.overlay.hidden,true);
  const event=h.submit(f.result);event.preventDefault();h.flush();assert.equal(h.overlay.hidden,true);
}
for(const [action,title] of [['restaurar','Restaurando registros…'],['excluir-definitivamente','Excluindo permanentemente…'],['enviar','Enviando e preparando o arquivo…']]) {
  const h=harness(),f=h.form('http://127.0.0.1:8080/importacoes/22/'+action);
  h.submit(f.result);h.flush();assert.equal(h.heading.textContent,title);
}
{
  const h=harness();
  function click(href, extra={}) {
    const link={href,hasAttribute:()=>false};
    h.events.click({target:{closest:()=>link},button:0,defaultPrevented:false,...extra});h.flush();
  }
  click('http://127.0.0.1:8080/clientes/exportar.csv');assert.equal(h.overlay.hidden,true);
  click('http://127.0.0.1:8080/importacoes/22#pendencias');assert.equal(h.overlay.hidden,true);
  click('http://127.0.0.1:8080/inicio',{ctrlKey:true});assert.equal(h.overlay.hidden,true);
  click('http://127.0.0.1:8080/inicio');assert.equal(h.overlay.hidden,true);
  click('http://127.0.0.1:8080/');assert.equal(h.overlay.hidden,true);
  const filter=h.form('http://127.0.0.1:8080/','get');
  h.submit(filter.result);h.flush();assert.equal(h.overlay.hidden,true);
}
console.log('OK: exclusão, restauração, validação, clique repetido, retorno, navegação e download sem bloqueio.');
