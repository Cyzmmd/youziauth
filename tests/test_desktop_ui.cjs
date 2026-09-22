// Exercise the actual UI script with fictional snapshots, never a real backend.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');

function harness(source='windows'){
  const html=fs.readFileSync(path.join(__dirname,'../desktop_ui/index.html'),'utf8');
  const elements=new Map(),actions=[];
  for(const match of html.matchAll(/<(\w+)\b([^>]*)>/g)){
    const attributes=Object.fromEntries([...match[2].matchAll(/([\w-]+)="([^"]*)"/g)].map(a=>[a[1],a[2]]));
    const id=attributes.id,action=attributes['data-action'];
    if(!id&&!action)continue;
    const classes=new Set();
    const element={
      id,tagName:match[1].toUpperCase(),value:attributes.value||'',checked:false,hidden:false,disabled:false,open:false,
      textContent:'',firstChild:{textContent:''},dataset:action?{action}:{},attributes,
      classList:{toggle(name,on){if(on)classes.add(name);else classes.delete(name);},contains(name){return classes.has(name);}},
      listeners:{},addEventListener(event,fn){this.listeners[event]=fn;},
      setAttribute(name,value){this.attributes[name]=value;},removeAttribute(name){delete this.attributes[name];},
      focus(){},showModal(){this.open=true;},close(){this.open=false;this.listeners.close?.();},
    };
    if(id)elements.set(id,element);
    if(action)actions.push(element);
  }
  const saveSource={id:'save-location-source',tagName:'BUTTON',disabled:false,textContent:'保存定位来源',
    listeners:{},addEventListener(event,fn){this.listeners[event]=fn;},focus(){},dataset:{}};
  elements.set('save-location-source',saveSource);
  for(const select of html.matchAll(/<select\b[^>]*id="([^"]+)"[^>]*>([\s\S]*?)<\/select>/g)){
    const element=elements.get(select[1]);
    element.options=[...select[2].matchAll(/<option\b[^>]*value="([^"]+)"[^>]*>([^<]*)<\/option>/g)].map(o=>({value:o[1],text:o[2]}));
    element.value=element.options[0]?.value||'';
  }
  const el=id=>{assert.ok(elements.has(id),'Missing UI node: '+id);return elements.get(id);};
  const stop=actions.find(button=>button.dataset.action==='network_stop');
  const data={preview:true,location:{state:'idle',message:'尚未检测',accuracy:null,checked:'',busy:false},network:{username:'saved',interval:60,startup:false,monitoring:true,busy:false,state:'online',message:'online',checked:'',has_password:true},dorm:{state:'idle',message:'pending',busy:false,task:null,settings:{enabled:false,start:'21:00',end:'23:30',interval:300,location_source:source},schedule:'off'},logs:{network:'',dorm:''}};
  const calls=[];
  const api={snapshot:async()=>structuredClone(data),dispatch:async(action,payload)=>{
    calls.push({action,payload});
    if(action==='dorm_save')Object.assign(data.dorm.settings,payload);
    if(action==='location_source_save')data.dorm.settings.location_source=payload.location_source;
    return {ok:true,message:'操作完成'};
  }};
  const context=vm.createContext({console,URLSearchParams,Intl,Date,Promise,
    location:{hash:'#network',search:''},setInterval(){},setTimeout(){},clearTimeout(){},
    window:{addEventListener(){},scrollTo(){},pywebview:{api}},
    document:{getElementById:id=>elements.get(id)||null,querySelectorAll:selector=>{
      if(selector==='[data-action]')return actions;
      const selected=[...selector.matchAll(/\[data-action="([^"]+)"\]/g)].map(m=>m[1]);
      if(selected.length)return actions.filter(button=>selected.includes(button.dataset.action));
      const ids=[...selector.matchAll(/#([\w-]+)/g)].map(m=>m[1]);
      return ids.map(id=>elements.get(id)).filter(Boolean);
    }},
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../desktop_ui/app.js'),'utf8'),context);
  return {el,data,api,calls,context,stop,refresh:()=>vm.runInContext('refresh()',context)};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));
function pickSource(h,source){h.el('location-source').value=source;h.el('location-source').listeners.change();}function saveSource(h){return h.el('save-location-source').listeners.click();}
function saveSchedule(h){return h.el('dorm-form').listeners.submit({preventDefault(){}});}

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

test('a successful network save without a snapshot preserves the draft and password',async()=>{
  const h=harness();await settle();
  h.api.snapshot=async()=>{throw Error('snapshot unavailable');};
  h.el('username').value='draft';h.el('password').value='draft-password';
  h.el('network-form').listeners.input();
  await h.el('network-form').listeners.submit({preventDefault(){}});
  assert.equal(h.el('username').value,'draft');
  assert.equal(h.el('password').value,'draft-password');
  assert.equal(h.el('network-save-state').textContent,'有未保存的修改');
  assert.match(h.el('toast').textContent,/操作已执行.*尚未同步/);
});

test('a startup change warns that Windows will ask for administrator approval',async()=>{
  const h=harness();await settle();
  let complete;
  h.api.dispatch=(action,payload)=>new Promise(resolve=>complete=()=>resolve({ok:true,message:'操作完成'}));
  h.el('startup').checked=true;
  const saving=h.el('network-form').listeners.submit({preventDefault(){}});
  assert.match(h.el('toast').textContent,/管理员授权/);
  complete();await saving;
});

test('saving without a startup change does not claim a UAC prompt',async()=>{
  const h=harness();await settle();
  h.el('username').value='student';
  h.el('network-form').listeners.input();
  await h.el('network-form').listeners.submit({preventDefault(){}});
  assert.doesNotMatch(h.el('toast').textContent,/管理员授权/);
});

test('location actions wait for the first snapshot',async()=>{
  const h=harness();
  h.el('authorize-location').listeners.click();h.el('submit-dorm').onclick();
  assert.equal(h.calls.length,0);
  assert.equal(h.el('confirm-dialog').open,false);
  assert.match(h.el('toast').textContent,/尚未同步/);
  await settle();
  h.el('authorize-location').listeners.click();await settle();
  assert.deepEqual(h.calls.map(call=>call.action),['location_authorize']);
});

test('native location select restores the saved source without performing actions',async()=>{
  const h=harness('simulation');await settle();
  const select=h.el('location-source');
  assert.equal(select.tagName,'SELECT');
  assert.deepEqual(select.options,[{value:'windows',text:'真实定位（Windows / Wi-Fi）'},{value:'simulation',text:'模拟定位（已保存样本）'}]);
  assert.equal(select.value,'simulation');
  assert.equal(h.calls.length,0);
  h.data.dorm.settings.location_source='windows';await h.refresh();
  assert.equal(select.value,'windows');
});

test('saving the source sends only the source and keeps the schedule untouched',async()=>{
  const h=harness();await settle();
  pickSource(h,'simulation');await saveSource(h);
  assert.equal(h.calls.length,1);
  assert.equal(h.calls[0].action,'location_source_save');
  assert.deepEqual(JSON.parse(JSON.stringify(h.calls[0].payload)),{location_source:'simulation'});
  assert.equal(h.el('location-source').value,'simulation');
  assert.equal(h.el('dorm-save-state').textContent,'设置已同步');
  assert.match(h.el('location-source-badge').textContent,/模拟/);
  assert.match(h.el('overview-location-source').textContent,/模拟/);
});

test('saving the schedule alone never rewrites the saved location source',async()=>{
  const h=harness('simulation');await settle();
  h.el('auto-interval').value='600';h.el('dorm-form').listeners.input();
  await saveSchedule(h);
  assert.equal(h.calls.length,1);
  assert.equal(h.calls[0].action,'dorm_save');
  assert.equal('location_source' in h.calls[0].payload,false);
  assert.equal(h.data.dorm.settings.location_source,'simulation');
  assert.match(h.el('location-source-badge').textContent,/模拟/);
});

test('the source save button stays idle until the draft differs from the saved source',async()=>{
  const h=harness();await settle();
  assert.equal(h.el('save-location-source').disabled,true);
  pickSource(h,'simulation');await h.refresh();
  assert.equal(h.el('save-location-source').disabled,false);
  assert.equal(h.el('dorm-save-state').textContent,'有未保存的修改');
  pickSource(h,'windows');await h.refresh();
  assert.equal(h.el('save-location-source').disabled,true);
});
test('refresh preserves a source draft but labels and detection use the saved source',async()=>{
  const h=harness();await settle();
  pickSource(h,'simulation');await h.refresh();
  assert.equal(h.el('location-source').value,'simulation');
  assert.match(h.el('location-source-badge').textContent,/真实.*Windows/);
  assert.equal(h.el('authorize-location').textContent,'授权并检测定位');
  assert.equal(h.calls.length,0);
});

test('a source edit during save survives and the accepted source drives the label',async()=>{
  const h=harness();await settle();
  let complete;
  h.api.dispatch=(action,payload)=>new Promise(resolve=>{complete=()=>{h.data.dorm.settings.location_source=payload.location_source;resolve({ok:true,message:'saved'});};});
  pickSource(h,'simulation');const saving=saveSource(h);
  pickSource(h,'windows');complete();await saving;
  assert.equal(h.el('location-source').value,'windows');
  assert.equal(h.el('dorm-save-state').textContent,'有未保存的修改');
  assert.match(h.el('location-source-badge').textContent,/模拟/);
  h.el('authorize-location').listeners.click();await settle();
  assert.match(h.el('toast').textContent,/先保存.*来源/);
});

test('failed source save keeps the draft while the saved source remains real',async()=>{
  const h=harness();await settle();
  h.api.dispatch=async()=>({ok:false,message:'failed'});
  pickSource(h,'simulation');await saveSource(h);
  assert.equal(h.el('location-source').value,'simulation');
  assert.equal(h.el('dorm-save-state').textContent,'有未保存的修改');
  assert.match(h.el('location-source-badge').textContent,/真实/);
});

test('saved simulation stays dirty and blocks location actions until its snapshot recovers',async()=>{
  const h=harness();await settle();
  h.data.dorm.state='ready';await h.refresh();
  const snapshot=h.api.snapshot;
  h.api.snapshot=async()=>{throw Error('snapshot unavailable');};
  pickSource(h,'simulation');await saveSource(h);
  assert.deepEqual(h.calls.map(call=>call.action),['location_source_save']);
  assert.equal(h.el('location-source').value,'simulation');
  assert.equal(h.el('dorm-save-state').textContent,'有未保存的修改');
  assert.equal(h.el('connection-error').hidden,false);
  assert.match(h.el('toast').textContent,/操作已执行.*尚未同步/);
  h.el('authorize-location').listeners.click();h.el('submit-dorm').onclick();await settle();
  assert.deepEqual(h.calls.map(call=>call.action),['location_source_save']);
  assert.equal(h.el('confirm-dialog').open,false);
  assert.equal(h.el('authorize-location').disabled,true);
  assert.equal(h.el('submit-dorm').disabled,true);
  assert.match(h.el('toast').textContent,/尚未同步/);

  h.api.snapshot=snapshot;await h.refresh();
  assert.equal(h.el('connection-error').hidden,true);
  assert.equal(h.el('location-source').value,'simulation');
  assert.match(h.el('location-source-badge').textContent,/模拟/);
  assert.equal(h.el('authorize-location').disabled,false);
  assert.equal(h.el('submit-dorm').disabled,false);
  assert.deepEqual(h.calls.map(call=>call.action),['location_source_save']);
  h.el('authorize-location').listeners.click();await settle();
  h.el('submit-dorm').onclick();
  assert.match(h.el('confirm-text').textContent,/已保存的模拟位置/);
  h.el('confirm-ok').onclick();await settle();
  assert.deepEqual(h.calls.map(call=>call.action),['location_source_save','location_authorize','dorm_submit']);
});

test('a failed snapshot blocks even a matching source and recovery reads the real saved source',async()=>{
  const h=harness();await settle();
  h.data.dorm.state='ready';await h.refresh();
  const snapshot=h.api.snapshot;
  h.api.snapshot=async()=>{throw Error('snapshot unavailable');};
  await h.refresh();
  h.el('authorize-location').listeners.click();h.el('submit-dorm').onclick();await settle();
  assert.equal(h.calls.length,0);
  assert.equal(h.el('confirm-dialog').open,false);
  assert.equal(h.el('authorize-location').disabled,true);
  assert.equal(h.el('submit-dorm').disabled,true);
  assert.equal(h.el('connection-error').hidden,false);
  assert.match(h.el('toast').textContent,/尚未同步/);

  h.data.dorm.settings.location_source='simulation';h.api.snapshot=snapshot;await h.refresh();
  assert.equal(h.el('location-source').value,'simulation');
  assert.match(h.el('location-source-badge').textContent,/模拟/);
  assert.equal(h.el('connection-error').hidden,true);
  assert.equal(h.el('authorize-location').disabled,false);
  assert.equal(h.el('submit-dorm').disabled,false);
});

test('simulation detection is clearly non-live and requires no Windows permission',async()=>{
  const h=harness('simulation');await settle();
  assert.equal(h.el('authorize-location').textContent,'检测模拟定位');
  assert.match(h.el('location-source-badge').textContent,/模拟/);
  assert.equal(h.el('location-source-badge').className,'badge full warning');
  // 冗长的模拟定位说明已移除：模拟模式下不再显示任何模式说明文字。
  assert.equal(h.el('location-mode-hint').hidden,true);
  h.el('authorize-location').listeners.click();await settle();
  assert.equal(h.calls[0]?.action,'location_authorize');
  assert.match(h.el('toast').textContent,/模拟.*非实时/);
  h.data.location={state:'checking',message:'检测中',accuracy:null,checked:'',busy:true};await h.refresh();
  assert.equal(h.el('authorize-location').disabled,true);
  assert.match(h.el('authorize-location').textContent,/检测模拟定位/);
  assert.doesNotMatch(h.el('authorize-location').textContent,/授权/);
  h.data.location={state:'ready',message:'检测通过',accuracy:25,checked:'21:10',busy:false};await h.refresh();
  assert.equal(h.el('authorize-location').disabled,false);
  assert.match(h.el('location-message').textContent,/模拟.*非实时/);
  assert.match(h.el('location-checked').textContent,/模拟.*21:10/);
});

test('saving Windows restores real detection and explains Windows chooses the source',async()=>{
  const h=harness('simulation');await settle();
  pickSource(h,'windows');await saveSource(h);
  assert.equal(h.calls[0].action,'location_source_save');
  assert.equal(h.calls[0].payload.location_source,'windows');
  assert.equal(h.el('authorize-location').textContent,'授权并检测定位');
  assert.match(h.el('location-source-badge').textContent,/真实.*Windows/);
  assert.doesNotMatch(h.el('location-source-badge').textContent,/模拟/);
  assert.match(h.el('location-mode-hint').textContent,/Windows.*决定/);
  assert.match(h.el('location-mode-hint').textContent,/不保证.*Wi-Fi/);
  assert.equal(h.el('location-mode-hint').hidden,false);
});

test('the removed verbose location paragraphs stay out of the markup',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../desktop_ui/index.html'),'utf8');
  assert.doesNotMatch(html,/location-submit-hint/);
  assert.doesNotMatch(html,/自动执行需电脑开机/);
  assert.doesNotMatch(html,/随机偏移/);
  assert.doesNotMatch(html,/已保存的模拟位置（非实时）/);
});

for(const saved of ['windows','simulation']){
  test(`unsaved source blocks detection and submission when saved source is ${saved}`,async()=>{
    const h=harness(saved);await settle();
    h.data.dorm.state='ready';await h.refresh();
    pickSource(h,saved==='windows'?'simulation':'windows');
    h.el('authorize-location').listeners.click();await settle();
    assert.equal(h.calls.length,0);
    assert.match(h.el('toast').textContent,/先保存.*来源/);
    h.el('toast').textContent='';h.el('submit-dorm').onclick();
    assert.equal(h.el('confirm-dialog').open,false);
    assert.equal(h.calls.length,0);
    assert.match(h.el('toast').textContent,/先保存.*来源/);
  });
}

test('unsaved schedule edits or a reverted source do not block detection or submission',async()=>{
  const h=harness();await settle();
  h.data.dorm.state='ready';await h.refresh();
  h.el('auto-interval').value='600';h.el('dorm-form').listeners.input();
  pickSource(h,'simulation');pickSource(h,'windows');
  h.el('authorize-location').listeners.click();await settle();
  assert.equal(h.calls[0]?.action,'location_authorize');
  h.el('submit-dorm').onclick();assert.equal(h.el('confirm-dialog').open,true);
  h.el('confirm-ok').onclick();await settle();
  assert.equal(h.calls[1]?.action,'dorm_submit');
});

for(const source of ['windows','simulation']){
  test(`${source} submission confirmation describes the saved source sent to school`,async()=>{
    const h=harness(source);await settle();
    h.data.dorm.state='ready';await h.refresh();
    h.el('submit-dorm').onclick();
    assert.equal(h.el('confirm-dialog').open,true);
    const text=h.el('confirm-text').textContent;
    assert.match(text,/向学校发送/);
    if(source==='simulation'){
      assert.match(text,/已保存的模拟位置/);assert.match(text,/非实时/);
      assert.doesNotMatch(text,/Windows 实时定位/);
    }else assert.match(text,/Windows 实时定位/);
    assert.equal(h.calls.length,0);
    h.el('confirm-ok').onclick();await settle();
    assert.equal(h.calls[0]?.action,'dorm_submit');
    if(source==='simulation')assert.match(h.el('toast').textContent,/模拟.*非实时/);
  });

  test(`${source} uncertain confirmation only queries an existing result`,async()=>{
    const h=harness(source);await settle();
    h.data.dorm.state='uncertain';await h.refresh();
    h.el('submit-dorm').onclick();
    assert.equal(h.el('confirm-title').textContent,'回查提交结果？');
    const text=h.el('confirm-text').textContent;
    assert.match(text,/仅.*查询.*已有.*结果/);
    assert.doesNotMatch(text,/将使用|实时定位|向学校发送.*位置/);
    h.el('confirm-ok').onclick();await settle();
    assert.deepEqual(h.calls.map(call=>call.action),['dorm_query']);
  });
}

test('uncertain confirmation only queries when the server has already replaced the task',async()=>{
  const h=harness();await settle();
  h.data.dorm.state='uncertain';
  h.data.dorm.task={title:'旧任务',date:'2026-09-01',start:'21:00',end:'23:30',address:'测试宿舍'};
  await h.refresh();h.el('submit-dorm').onclick();
  h.data.dorm.state='ready';
  h.data.dorm.task={title:'新任务',date:'2026-09-02',start:'21:00',end:'23:30',address:'测试宿舍'};
  assert.equal(h.el('task-title').textContent,'旧任务');
  h.el('confirm-ok').onclick();await settle();
  assert.deepEqual(h.calls.map(call=>call.action),['dorm_query']);
});

test('confirmation cannot submit a new unsaved source draft',async()=>{
  const h=harness();await settle();
  h.data.dorm.state='ready';await h.refresh();h.el('submit-dorm').onclick();
  pickSource(h,'simulation');h.el('confirm-ok').onclick();await settle();
  assert.equal(h.calls.length,0);
  assert.match(h.el('toast').textContent,/先保存.*来源/);
});

test('confirmation cannot silently use a different saved source',async()=>{
  const h=harness();await settle();
  h.data.dorm.state='ready';await h.refresh();h.el('submit-dorm').onclick();
  h.data.dorm.settings.location_source='simulation';await h.refresh();
  h.el('confirm-ok').onclick();await settle();
  assert.equal(h.calls.length,0);
  assert.match(h.el('toast').textContent,/重新确认/);
});

test('an uncertain confirmation cannot turn into a new submission after refresh',async()=>{
  const h=harness();await settle();
  h.data.dorm.state='uncertain';await h.refresh();h.el('submit-dorm').onclick();
  h.data.dorm.state='ready';await h.refresh();
  h.el('confirm-ok').onclick();await settle();
  assert.equal(h.calls.length,0);
  assert.match(h.el('toast').textContent,/重新确认/);
});

for(const state of ['ready','uncertain']){
  test(`${state} confirmation cannot execute after a snapshot failure`,async()=>{
    const h=harness();await settle();
    h.data.dorm.state=state;await h.refresh();h.el('submit-dorm').onclick();
    assert.equal(h.el('confirm-dialog').open,true);
    h.api.snapshot=async()=>{throw Error('snapshot unavailable');};
    await h.refresh();h.el('confirm-ok').onclick();await settle();
    assert.equal(h.calls.length,0);
    assert.match(h.el('toast').textContent,/尚未同步/);
  });
}

test('a stale snapshot cannot unlock location actions while the current snapshot is pending',async()=>{
  const h=harness();await settle();
  h.data.dorm.state='ready';await h.refresh();
  let completeOld,completeCurrent;
  h.api.snapshot=()=>new Promise(resolve=>completeOld=resolve);
  const refreshing=h.refresh();
  h.stop.listeners.click();await settle();
  h.api.snapshot=()=>new Promise(resolve=>completeCurrent=resolve);
  completeOld(structuredClone(h.data));await refreshing;await settle();
  h.el('authorize-location').listeners.click();h.el('submit-dorm').onclick();await settle();
  assert.deepEqual(h.calls.map(call=>call.action),['network_stop']);
  assert.equal(h.el('confirm-dialog').open,false);
  assert.match(h.el('toast').textContent,/尚未同步/);
  completeCurrent(structuredClone(h.data));await settle();
  assert.equal(h.el('authorize-location').disabled,false);
  assert.equal(h.el('submit-dorm').disabled,false);
});

test('a stale snapshot rejection does not change the current connection status',async()=>{
  const h=harness();await settle();
  const snapshot=h.api.snapshot;
  let rejectOld,completeAction;
  h.api.snapshot=()=>new Promise((resolve,reject)=>rejectOld=reject);
  const refreshing=h.refresh();
  h.api.dispatch=async(action,payload)=>{
    h.calls.push({action,payload});await new Promise(resolve=>completeAction=resolve);
    return {ok:true,message:'操作完成'};
  };
  h.stop.listeners.click();
  rejectOld(Error('obsolete snapshot failed'));await refreshing;
  assert.equal(h.el('connection-error').hidden,true);
  h.api.snapshot=snapshot;completeAction();await settle();
  assert.equal(h.el('connection-error').hidden,true);
  assert.equal(h.el('authorize-location').disabled,false);
});

test('a snapshot obtained during dispatch cannot authorize confirmation after dispatch completes',async()=>{
  const h=harness();await settle();
  h.data.dorm.state='ready';await h.refresh();h.el('submit-dorm').onclick();
  let completeAction,completeSnapshot;
  h.api.dispatch=async(action,payload)=>{
    h.calls.push({action,payload});
    if(action==='network_stop')await new Promise(resolve=>completeAction=resolve);
    return {ok:true,message:'操作完成'};
  };
  h.stop.listeners.click();await h.refresh();
  h.api.snapshot=()=>new Promise(resolve=>completeSnapshot=resolve);
  completeAction();await settle();
  h.el('confirm-ok').onclick();await settle();
  assert.deepEqual(h.calls.map(call=>call.action),['network_stop']);
  assert.match(h.el('toast').textContent,/尚未同步/);
  completeSnapshot(structuredClone(h.data));await settle();
  assert.equal(h.el('submit-dorm').disabled,false);
});
