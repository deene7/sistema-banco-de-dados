const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const settle=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  const token='a'.repeat(32),badge={textContent:''},events={},windows={},timers=new Map();let id=0,requests=0;
  const row={querySelector:()=>badge};
  const panel={dataset:{statusUrl:'/consultas-planilha/status?id='+token},querySelector:selector=>selector==='[data-lookup-id]'||selector==='[data-lookup-id="'+token+'"]'?row:null};
  const document={hidden:false,querySelector:()=>panel,addEventListener:(name,fn)=>events[name]=fn};
  const responses=[{jobs:[{token,estado:'processando'}]},{jobs:[{token,estado:'concluida'}]}];
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../static/lookup-jobs.js'),'utf8'),{
    document,AbortSignal,window:{addEventListener:(name,fn)=>windows[name]=fn},
    fetch:async()=>{requests++;const data=responses.shift();return{ok:true,status:200,redirected:false,json:async()=>data};},
    setTimeout:(fn,delay)=>{const key=++id;timers.set(key,{fn,delay});return key;},clearTimeout:key=>timers.delete(key)
  });
  const [first,timer]=timers.entries().next().value;timers.delete(first);await timer.fn();await settle();
  assert.equal(badge.textContent,'Processando');
  document.hidden=true;events.visibilitychange();assert.equal(timers.size,0);
  document.hidden=false;events.visibilitychange();await settle();
  assert.equal(requests,2);assert.equal(badge.textContent,'Concluída');
  assert.equal([...timers.values()][0].delay,15000);
  windows.pagehide();assert.equal(timers.size,0);
  console.log('OK: atualização das consultas em lote, pausa em aba oculta e retomada sem requisições sobrepostas.');
})().catch(error=>{console.error(error);process.exitCode=1;});
