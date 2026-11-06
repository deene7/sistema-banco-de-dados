const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../static/theme.js'),'utf8');
function setup(saved,blocked=false){
  const events={},windowEvents={},clicks={},attrs={},writes=[],root={dataset:{}},label={textContent:''};
  const button={setAttribute:(k,v)=>attrs[k]=v,querySelector:()=>label,addEventListener:(k,f)=>clicks[k]=f};
  let ready=false;
  const document={documentElement:root,querySelectorAll:()=>ready?[button]:[],addEventListener:(k,f)=>events[k]=f};
  vm.runInNewContext(source,{document,window:{addEventListener:(k,f)=>windowEvents[k]=f},localStorage:{getItem:()=>{if(blocked)throw Error();return saved},setItem:(k,v)=>{if(blocked)throw Error();writes.push([k,v])}}});
  return{root,attrs,writes,label,ready(){ready=true;events.DOMContentLoaded()},click(){clicks.click()},storage:windowEvents.storage};
}
const dark=setup('dark');assert.equal(dark.root.dataset.theme,'dark','Aplica antes do carregamento');dark.ready();
assert.equal(dark.attrs['aria-pressed'],'true');assert.equal(dark.label.textContent,'Modo claro');
dark.click();assert.equal(dark.root.dataset.theme,'light');assert.deepEqual(dark.writes,[['wa_theme','light']]);
dark.storage({key:'wa_theme',newValue:'dark'});assert.equal(dark.root.dataset.theme,'dark');
dark.storage({key:'outro',newValue:'light'});assert.equal(dark.root.dataset.theme,'dark');
dark.storage({key:'wa_theme',newValue:null});assert.equal(dark.root.dataset.theme,'light');
const blocked=setup('dark',true);blocked.ready();blocked.click();assert.equal(blocked.root.dataset.theme,'dark');
const invalid=setup('invalid');assert.equal(invalid.root.dataset.theme,'light');
console.log('OK: tema antes do render, alternancia, persistencia, sincronizacao entre abas e armazenamento indisponivel.');
