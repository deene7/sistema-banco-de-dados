const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const settle=()=>new Promise(resolve=>setImmediate(resolve));
class Element {
  constructor(dataset={}){this.dataset=dataset;this.children=[];this.hidden=false;this.textContent='';this.map={};this.lists={};}
  querySelector(key){return this.map[key]||null;}
  querySelectorAll(key){return this.lists[key]||[];}
  appendChild(child){this.children.push(child);}
  replaceChildren(fragment){this.children=[...fragment.children];}
  removeAttribute(key){delete this[key];}
}
function run(file,panel,responses,extras={}){
  const events={},windowEvents={},timers=new Map();let requests=0,next=0;
  const document={hidden:false,querySelector:()=>panel,querySelectorAll:selector=>extras[selector]||[],
    addEventListener:(key,fn)=>events[key]=fn,createElement:()=>new Element(),createDocumentFragment:()=>new Element()};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../static',file),'utf8'),{
    document,AbortSignal,URLSearchParams,Date,console,
    window:{addEventListener:(key,fn)=>windowEvents[key]=fn},
    fetch:async()=>{requests++;const data=responses.shift();assert.ok(data,'Consulta inesperada');if(data instanceof Error)throw data;return{ok:true,status:200,redirected:false,json:async()=>data};},
    setTimeout:(fn,delay)=>{const id=++next;timers.set(id,{fn,delay});return id;},clearTimeout:id=>timers.delete(id)
  });
  return{document,events,windowEvents,timers,get requests(){return requests;},async tick(){const [id,timer]=timers.entries().next().value;timers.delete(id);await timer.fn();await settle();}};
}
(async()=>{
  const panel=new Element({statusUrl:'/qualidade/resumo'});
  const total=new Element({qualityKey:'total'}),percent=new Element({qualityPercent:'cobertura_telefone'}),body=new Element(),message=new Element();
  panel.lists['[data-quality-key]']=[total];panel.lists['[data-quality-percent]']=[percent];panel.map['[data-quality-categories]']=body;panel.map['[data-quality-updated]']=message;
  const quality=run('quality.js',panel,[{stats:null},{stats:{total:12,cobertura_telefone:50,categorias:[{id:1,nome:'<script>não executar</script>',clientes:12,verificados:6,cobertura:50}],atualizado_em:'04/11/2026 12:00',},refreshing:false}]);
  await settle();await quality.tick();
  assert.equal(total.textContent,'12');assert.equal(percent.value,50);
  assert.equal(body.children[0].children[0].textContent,'<script>não executar</script>');
  assert.equal(body.children[0].children[4].children[0].href,'/?categoria=1');
  assert.equal([...quality.timers.values()][0].delay,120000);
  quality.document.hidden=true;quality.events.visibilitychange();assert.equal(quality.timers.size,0);
  const center=new Element({statusUrl:'/processamentos/status?id=1'}),row=new Element({jobId:'1'}),count=new Element({processingCount:'fila'});
  const fields=['state','progress','percent','time','resume','review'];fields.forEach(field=>row.map['[data-job-'+field+']']=new Element());
  center.map['[data-job-id]']=row;center.map['[data-job-id="1"]']=row;center.lists['[data-job-id]']=[row];center.map['[data-processing-message]']=new Element();
  const operations=run('processing.js',center,[
    {jobs:[{id:1,situacao:'fila',estado:'preparando',progresso:0,meta:0,percentual:null,ativo:true,posicao:1,retomar:false}],totals:{fila:1}},
    {jobs:[{id:1,situacao:'interrompido',estado:'importando',progresso:5,meta:10,percentual:50,ativo:false,retomar:true}],totals:{fila:0}},
    new Error('Rede indisponível')
  ],{'[data-processing-count]':[count]});
  await operations.tick();assert.equal(row.map['[data-job-state]'].textContent,'Na fila · posição 1');assert.equal(count.textContent,'1');assert.equal(row.map['[data-job-resume]'].hidden,true);
  operations.document.hidden=true;operations.events.visibilitychange();assert.equal(operations.timers.size,0);
  operations.document.hidden=false;operations.events.visibilitychange();await settle();
  assert.equal(row.map['[data-job-state]'].textContent,'Interrompido');assert.equal(row.map['[data-job-resume]'].hidden,false);assert.equal(row.map['[data-job-percent]'].hidden,true);
  await operations.tick();assert.ok(center.map['[data-processing-message]'].textContent.includes('Tentaremos'));
  operations.windowEvents.pagehide();assert.equal(operations.timers.size,0);
  console.log('OK: indicadores sem HTML dinâmico, cache periódico, fila, retomada, aba oculta e recuperação de rede.');
})().catch(error=>{console.error(error);process.exitCode=1;});
