const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
let selected='telefones';const handlers=[],phone=[{hidden:false},{hidden:false}],cpf=[{hidden:true}],verification={disabled:false,value:'sem'};
const choices=[0,1].map(()=>({addEventListener:(_,handler)=>handlers.push(handler)}));
const document={querySelectorAll:key=>key==='input[name="tipo"]'?choices:key==='[data-cpf-guide]'?cpf:phone,
  querySelector:key=>key.includes(':checked')?{value:selected}:verification};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../static/lookup-form.js'),'utf8'),{document});
assert.equal(verification.disabled,false);assert.equal(cpf[0].hidden,true);
selected='cpf';handlers[1]();assert.ok(phone.every(item=>item.hidden));assert.equal(cpf[0].hidden,false);assert.equal(verification.disabled,true);
selected='telefones';handlers[0]();assert.ok(phone.every(item=>!item.hidden));assert.equal(verification.disabled,false);assert.equal(verification.value,'sem');
console.log('OK: selecao CPF/telefones atualiza filtros e explicacoes sem perder a selecao anterior.');
