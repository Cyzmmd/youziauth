// Exercise the actual UI script's save races and stop guard without a real backend.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');

function harness(){
  const elements=new Map();
  function el(id){
    if(!elements.has(id))elements.set(id,{
      id,value:'',checked:false,hidden:false,textContent:'',firstChild:{textContent:''},dataset:{},
      classList:{toggle(){}},listeners:{},addEventListener(event,fn){this.listeners[event]=fn;},
      setAttribute(){},removeAttribute(){},focus(){},showModal(){},close(){},
    });
    return elements.get(id);
  }
  const stop=el('stop');stop.dataset.action='network_stop';
  const data={preview:true,network:{username:'saved',interval:60,startup:false,monitoring:true,busy:false,state:'online',message:'online',checked:'',has_password:true},dorm:{state:'idle',message:'pending',busy:false,task:null,settings:{enabled:false,start:'21:00',end:'23:30',interval:300},schedule:'off'},logs:{network:'',dorm:''}};
  const calls=[];
  const api={snapshot:async()=>structuredClone(data),dispatch:async(action,payload)=>{calls.push({action,payload});return {ok:true,message:'saved'};}};
  const context=vm.createContext({console,URLSearchParams,Intl,Date,Promise,
    location:{hash:'#network',search:''},setInterval(){},setTimeout(){},clearTimeout(){},
    window:{addEventListener(){},scrollTo(){},pywebview:{api}},
    document:{getElementById:el,querySelectorAll:selector=>selector==='[data-action]'?[stop]:[]},
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../desktop_ui/app.js'),'utf8'),context);
  return {el,data,api,calls,context,stop};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));

test('unsaved network form does not block stopping an active monitor',async()=>{
  const h=harness();await settle();
  h.el('network-form').listeners.input();
  h.stop.listeners.click();await settle();
  assert.equal(h.calls[0]?.action,'network_stop');
});

test('an edit made during network save remains dirty and is not replaced',async()=>{
  const h=harness();await settle();
  let complete;h.api.dispatch=()=>new Promise(resolve=>complete=resolve);
  h.el('username').value='submitted';h.el('password').value='first';
  h.el('network-form').listeners.input();
  const saving=h.el('network-form').listeners.submit({preventDefault(){}});
  h.el('username').value='later-edit';h.el('password').value='later-password';
  h.el('network-form').listeners.input();
  complete({ok:true,message:'saved'});await saving;
  assert.equal(h.el('username').value,'later-edit');
  assert.equal(h.el('password').value,'later-password');
  assert.equal(h.el('network-save-state').textContent,'有未保存的修改');
});

test('an edit made during dorm save remains dirty',async()=>{
  const h=harness();await settle();
  let complete;h.api.dispatch=()=>new Promise(resolve=>complete=resolve);
  h.el('dorm-form').listeners.input();
  const saving=h.el('dorm-form').listeners.submit({preventDefault(){}});
  h.el('auto-interval').value='600';h.el('dorm-form').listeners.input();
  complete({ok:true,message:'saved'});await saving;
  assert.equal(h.el('auto-interval').value,'600');
  assert.equal(h.el('dorm-save-state').textContent,'有未保存的修改');
});

test('failed save preserves the draft and password',async()=>{
  const h=harness();await settle();
  h.api.dispatch=async()=>({ok:false,message:'failed'});
  h.el('username').value='draft';h.el('password').value='draft-password';
  h.el('network-form').listeners.input();
  await h.el('network-form').listeners.submit({preventDefault(){}});
  assert.equal(h.el('username').value,'draft');
  assert.equal(h.el('password').value,'draft-password');
  assert.equal(h.el('network-save-state').textContent,'有未保存的修改');
});
