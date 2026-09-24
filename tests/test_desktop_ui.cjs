// Exercise the actual UI script with fictional snapshots, never a real backend.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');

function updateFixture(overrides={}){
  return {state:'idle',current_version:'1.4.4',latest_version:'',progress:0,downloaded_bytes:0,total_bytes:0,checked:'',message:'等待后台检查更新。',busy:false,...overrides};
}
function harness(source='windows'){
  const html=fs.readFileSync(path.join(__dirname,'../desktop_ui/index.html'),'utf8');
  const elements=new Map(),actions=[],navigation=[];
  for(const match of html.matchAll(/<(\w+)\b([^>]*)>/g)){
    const attributes=Object.fromEntries([...match[2].matchAll(/([\w-]+)="([^"]*)"/g)].map(a=>[a[1],a[2]]));
    const id=attributes.id,action=attributes['data-action'];
    const classes=new Set((attributes.class||'').split(/\s+/));
    if(!id&&!action&&!classes.has('nav-link'))continue;
    const element={
      id,tagName:match[1].toUpperCase(),value:attributes.value||'',checked:false,
      hidden:/\shidden(?:\s|$)/.test(match[2]),disabled:/\sdisabled(?:\s|$)/.test(match[2]),open:false,
      textContent:'',firstChild:{textContent:''},dataset:action?{action}:{},attributes,hash:attributes.href||'',
      options:[],children:[],
      appendChild(child){this.children.push(child);if(child.tagName==='OPTION')this.options.push(child);return child;},
      replaceChildren(...nodes){this.children=nodes;this.options=nodes.filter(node=>node.tagName==='OPTION');},
      set innerHTML(value){assert.fail('Backend text must never be rendered as HTML: '+value);},
      classList:{toggle(name,on){if(on)classes.add(name);else classes.delete(name);},contains(name){return classes.has(name);}},
      listeners:{},addEventListener(event,fn){this.listeners[event]=fn;},
      setAttribute(name,value){this.attributes[name]=value;},removeAttribute(name){delete this.attributes[name];},
      focus(){},showModal(){this.open=true;},close(){this.open=false;this.listeners.close?.();},
    };
    if(id)elements.set(id,element);
    if(action)actions.push(element);
    if(classes.has('nav-link'))navigation.push(element);
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
  const data={preview:true,update:updateFixture(),location:{state:'idle',message:'尚未检测',accuracy:null,checked:'',busy:false},network:{username:'saved',interval:60,startup:false,monitoring:true,busy:false,state:'online',message:'online',checked:'',has_password:true},dorm:{state:'idle',message:'pending',busy:false,task:null,settings:{enabled:false,start:'21:00',end:'23:30',interval:300,location_source:source},schedule:'off'},logs:{network:'',dorm:''}};
  const calls=[];
  const map={ok:true,point:{latitude:29.823693,longitude:106.422310,accuracy:100,source:'MAP_PICK',picked:true},
    reference:{latitude:29.823940,longitude:106.422470,address:'示例宿舍',radius_m:800},
    distance_m:31.6,in_range:true,
    points:[{id:'p1',name:'本机采样样本',latitude:29.823693,longitude:106.422310,accuracy:100,
             source:'SAMPLE',saved_at:'2026-09-24T09:40:00+08:00',active:true}],
    active_id:'p1',
    tile_url:'https://tile.openstreetmap.org/{z}/{x}/{y}.png',attribution:'© OpenStreetMap contributors',max_zoom:19};
  const api={snapshot:async()=>structuredClone(data),dispatch:async(action,payload)=>{
    calls.push({action,payload});
    if(action==='dorm_save')Object.assign(data.dorm.settings,payload);
    if(action==='location_source_save')data.dorm.settings.location_source=payload.location_source;
    if(action==='simulation_point_save'){
      if(payload.latitude===null||typeof payload.latitude!=='number')return {ok:false,message:'选点坐标无效，请在地图上重新选择。'};
      const named=payload.name||'选点 2';
      const existing=map.points.find(point=>point.name===named);
      map.points=map.points.map(point=>({...point,active:false,
        ...(existing&&point.id===existing.id?{latitude:payload.latitude,longitude:payload.longitude}:{})}));
      if(existing){map.active_id=existing.id;}
      else{
        map.points.push({id:'p'+(map.points.length+1),name:named,latitude:payload.latitude,
                         longitude:payload.longitude,accuracy:100,source:'MAP_PICK',
                         saved_at:'2026-09-24T10:00:00+08:00',active:true});
        map.active_id=map.points[map.points.length-1].id;
      }
      const chosen=map.points.find(point=>point.id===map.active_id);
      map.point={latitude:chosen.latitude,longitude:chosen.longitude,accuracy:100,source:'MAP_PICK',picked:true};
    }
    if(action==='simulation_point_select'){
      map.points=map.points.map(point=>({...point,active:point.id===payload.id}));
      const chosen=map.points.find(point=>point.active);
      if(!chosen)return {ok:false,message:'找不到这个选点，请刷新后重试。'};
      map.active_id=chosen.id;
      map.point={latitude:chosen.latitude,longitude:chosen.longitude,accuracy:100,
                 source:chosen.source,picked:true};
    }
    if(action==='simulation_point_rename'){
      if(!payload.name)return {ok:false,message:'名称不能为空，最多 24 个字。'};
      map.points=map.points.map(point=>point.id===payload.id?{...point,name:payload.name}:point);
    }
    if(action==='simulation_point_delete'){
      map.points=map.points.filter(point=>point.id!==payload.id);
      if(map.active_id===payload.id){
        map.active_id=map.points.length?map.points[0].id:'';
        map.points=map.points.map(point=>({...point,active:point.id===map.active_id}));
        const chosen=map.points.find(point=>point.active);
        if(chosen)map.point={latitude:chosen.latitude,longitude:chosen.longitude,accuracy:100,
                             source:chosen.source,picked:true};
      }
    }
    return {ok:true,message:'操作完成'};
  },simulation_map:async()=>structuredClone(map)};
  const context=vm.createContext({console,URLSearchParams,Intl,Date,Promise,
    location:{hash:'#network',search:''},setInterval(){},setTimeout(){},clearTimeout(){},
    window:{addEventListener(){},scrollTo(){},pywebview:{api}},
    document:{getElementById:id=>elements.get(id)||null,
      createElement(tag){return {tagName:String(tag).toUpperCase(),value:'',textContent:'',selected:false};},
      querySelectorAll:selector=>{
      if(selector==='[data-action]')return actions;
      if(selector==='.nav-link')return navigation;
      const selected=[...selector.matchAll(/\[data-action="([^"]+)"\]/g)].map(m=>m[1]);
      if(selected.length)return actions.filter(button=>selected.includes(button.dataset.action));
      const ids=[...selector.matchAll(/#([\w-]+)/g)].map(m=>m[1]);
      return ids.map(id=>elements.get(id)).filter(Boolean);
    }},
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../desktop_ui/app.js'),'utf8'),context);
  return {el,data,api,map,calls,context,stop,navigation,refresh:()=>vm.runInContext('refresh()',context),navigate(page){context.location.hash='#'+page;vm.runInContext('navigate()',context);}};
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
  assert.match(h.el('overview-location-source').textContent,/模拟/);
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
  assert.match(h.el('overview-location-source').textContent,/模拟/);
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
  assert.match(h.el('overview-location-source').textContent,/真实.*Windows/);
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
  assert.match(h.el('overview-location-source').textContent,/模拟/);
  h.el('authorize-location').listeners.click();await settle();
  assert.match(h.el('toast').textContent,/先保存.*来源/);
});

test('failed source save keeps the draft while the saved source remains real',async()=>{
  const h=harness();await settle();
  h.api.dispatch=async()=>({ok:false,message:'failed'});
  pickSource(h,'simulation');await saveSource(h);
  assert.equal(h.el('location-source').value,'simulation');
  assert.equal(h.el('dorm-save-state').textContent,'有未保存的修改');
  assert.match(h.el('overview-location-source').textContent,/真实/);
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
  assert.match(h.el('overview-location-source').textContent,/模拟/);
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
  assert.match(h.el('overview-location-source').textContent,/模拟/);
  assert.equal(h.el('connection-error').hidden,true);
  assert.equal(h.el('authorize-location').disabled,false);
  assert.equal(h.el('submit-dorm').disabled,false);
});

test('simulation detection is clearly non-live and requires no Windows permission',async()=>{
  const h=harness('simulation');await settle();
  assert.equal(h.el('authorize-location').textContent,'检测模拟定位');
  assert.match(h.el('overview-location-source').textContent,/模拟/);
  // 定位来源卡片只保留选择器、保存按钮与检测框：说明行与来源徽标已删除。
  const markup=fs.readFileSync(path.join(__dirname,'../desktop_ui/index.html'),'utf8');
  assert.doesNotMatch(markup,/location-source-badge|location-source-scope/);
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
  assert.match(h.el('overview-location-source').textContent,/真实.*Windows/);
  assert.doesNotMatch(h.el('overview-location-source').textContent,/模拟/);
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

async function updateHarness(overrides={}){
  const h=harness();await settle();
  h.data.update=updateFixture(overrides);await h.refresh();
  return h;
}
const readyUpdate={state:'ready',latest_version:'1.5.0',progress:100,downloaded_bytes:10485760,total_bytes:10485760,checked:'2026-09-15 09:30',message:'安装包已下载，哈希与签名验证通过。'};

function clickUpdate(h,id){
  const button=h.el(id);
  assert.equal(typeof button.onclick,'function','Update buttons need a guarded handler');
  button.onclick();
}

test('updates navigation selects its own page and retains the sidebar link',async()=>{
  const h=await updateHarness();h.navigate('updates');
  assert.equal(h.el('page-context').textContent,'软件更新');
  assert.equal(h.el('updates').hidden,false);
  assert.equal(h.el('network').hidden,true);
  const link=h.navigation.find(link=>link.hash==='#updates');
  assert.ok(link,'Software updates must be reachable from navigation');
  assert.equal(link.attributes['aria-current'],'page');
});

test('update actions are disabled and cannot run before the first snapshot',async()=>{
  const h=harness();
  assert.equal(h.el('check-updates').disabled,true);
  assert.equal(h.el('install-update').disabled,true);
  assert.equal(h.el('install-update').hidden,true);
  clickUpdate(h,'check-updates');clickUpdate(h,'install-update');
  assert.equal(h.calls.length,0);
  assert.equal(h.el('confirm-dialog').open,false);
  assert.match(h.el('toast').textContent,/尚未同步/);
  await settle();
});

test('idle updates show current version and offer an explicit check without auto-dispatch',async()=>{
  const h=await updateHarness();
  assert.equal(h.el('update-current-version').textContent,'1.4.4');
  assert.match(h.el('update-latest-version').textContent,/尚未检查/);
  assert.match(h.el('update-status').textContent,/等待检查/);
  assert.equal(h.el('update-message').textContent,'等待后台检查更新。');
  assert.match(h.el('update-checked').textContent,/尚未检查/);
  assert.equal(h.el('check-updates').disabled,false);
  assert.equal(h.el('update-notice').hidden,true);
  assert.equal(h.el('install-update').hidden,true);
  assert.equal(h.calls.length,0);
  clickUpdate(h,'check-updates');await settle();
  assert.deepEqual(JSON.parse(JSON.stringify(h.calls)),[{action:'update_check',payload:{}}]);
});

test('up-to-date state never offers a downgrade when the release is older',async()=>{
  const h=await updateHarness({state:'up_to_date',latest_version:'1.4.3',checked:'2026-09-15 09:30',message:'当前版本已是最新，无需更新。'});
  assert.equal(h.el('update-latest-version').textContent,'1.4.3');
  assert.match(h.el('update-status').textContent,/无需更新/);
  assert.match(h.el('update-message').textContent,/无需更新/);
  assert.match(h.el('update-checked').textContent,/2026-09-15 09:30/);
  assert.equal(h.el('install-update').hidden,true);
  assert.equal(h.el('update-notice').hidden,true);
  clickUpdate(h,'install-update');await settle();
  assert.equal(h.calls.length,0);
  assert.equal(h.el('confirm-dialog').open,false);
});

test('update errors persist across pages without poll toasts and allow a retry',async()=>{
  const message='GitHub 下载失败：连接超时，请重新检查后重试。';
  const h=await updateHarness({state:'error',message});
  assert.match(h.el('update-status').textContent,/更新未完成/);
  assert.equal(h.el('update-message').textContent,message);
  assert.equal(h.el('check-updates').textContent,'重新检查');
  assert.equal(h.el('check-updates').disabled,false);
  assert.equal(h.el('install-update').hidden,true);
  for(const page of ['overview','network','dorm','records','updates']){
    h.navigate(page);await h.refresh();
    assert.equal(h.el('update-notice').hidden,false);
    assert.equal(h.el('update-notice').classList.contains('error'),true);
    assert.ok(h.el('update-notice-text').textContent.includes(message));
    assert.equal(h.el('update-notice-link').attributes.href,'#updates');
    assert.equal(h.el('toast').hidden,true);
  }
  clickUpdate(h,'check-updates');await settle();
  assert.deepEqual(JSON.parse(JSON.stringify(h.calls)),[{action:'update_check',payload:{}}]);
  h.data.update=updateFixture({state:'checking',busy:true});await h.refresh();
  assert.equal(h.el('update-notice').hidden,true);
});

for(const [state,label] of [['checking','正在检查'],['downloading','正在下载'],['verifying','正在验证'],['launching','正在打开安装向导']]){
  test(`${state} updates disable checks and cannot open installation confirmation`,async()=>{
    const h=await updateHarness({state,busy:true,message:'正在处理，请稍候。'});
    assert.equal(h.el('update-status').textContent,label);
    assert.equal(h.el('check-updates').disabled,true);
    assert.equal(h.el('install-update').hidden,true);
    assert.equal(h.el('install-update').disabled,true);
    clickUpdate(h,'check-updates');clickUpdate(h,'install-update');await settle();
    assert.equal(h.calls.length,0);
    assert.equal(h.el('confirm-dialog').open,false);
  });
}

test('download progress exposes percentage and transferred size with an accessible progress element',async()=>{
  const h=await updateHarness({state:'downloading',busy:true,latest_version:'1.5.0',progress:40,downloaded_bytes:4194304,total_bytes:10485760});
  const progress=h.el('update-progress');
  assert.equal(progress.tagName,'PROGRESS');
  assert.equal(progress.attributes.max,'100');
  assert.equal(progress.attributes['aria-labelledby'],'update-progress-label');
  assert.equal(progress.hidden,false);
  assert.equal(progress.value,40);
  assert.match(h.el('update-progress-label').textContent,/40%/);
  assert.equal(h.el('update-download-size').textContent,'4.0 MiB / 10.0 MiB');
  h.data.update.downloaded_bytes=512;h.data.update.total_bytes=0;await h.refresh();
  assert.match(h.el('update-download-size').textContent,/512 B.*总大小未知/);
});

test('ready updates show a quiet global prompt and cancellation never dispatches',async()=>{
  const h=await updateHarness(readyUpdate);h.navigate('overview');
  assert.equal(h.el('update-status').textContent,'可以安装');
  assert.equal(h.el('install-update').hidden,false);
  assert.equal(h.el('install-update').disabled,false);
  assert.equal(h.el('update-notice').hidden,false);
  assert.match(h.el('update-notice-text').textContent,/1\.5\.0.*确认/);
  assert.equal(h.el('update-notice-link').attributes.href,'#updates');
  assert.equal(h.el('update-notice').classList.contains('error'),false);
  await h.refresh();await h.refresh();
  assert.equal(h.el('toast').hidden,true);
  assert.equal(h.calls.length,0);
  clickUpdate(h,'install-update');
  assert.equal(h.el('confirm-dialog').open,true);
  assert.match(h.el('confirm-title').textContent,/1\.5\.0/);
  const text=h.el('confirm-text').textContent;
  assert.match(text,/Windows.*管理员授权/);
  assert.match(text,/安装期间.*关闭.*程序.*系统代理/);
  assert.match(text,/完成后.*重新打开/);
  h.el('confirm-cancel').onclick();h.el('confirm-ok').onclick();await settle();
  assert.equal(h.el('confirm-dialog').open,false);
  assert.equal(h.calls.length,0);
});

test('installation confirms exactly the displayed version after an unchanged poll',async()=>{
  const h=await updateHarness(readyUpdate);clickUpdate(h,'install-update');
  await h.refresh();
  h.el('confirm-ok').onclick();h.el('confirm-ok').onclick();await settle();
  assert.deepEqual(JSON.parse(JSON.stringify(h.calls)),[{action:'update_install',payload:{confirmed:true,version:'1.5.0'}}]);
});

for(const [name,change] of [['version',{latest_version:'1.6.0'}],['state',{state:'error',message:'安装包已失效，请重新检查。'}]]){
  test(`installation rejects a changed ${name} after confirmation opened`,async()=>{
    const h=await updateHarness(readyUpdate);clickUpdate(h,'install-update');
    Object.assign(h.data.update,change);await h.refresh();
    h.el('confirm-ok').onclick();await settle();
    assert.equal(h.calls.length,0);
    assert.match(h.el('toast').textContent,/重新确认/);
  });
}

for(const openFirst of [false,true]){
  test(`snapshot failure blocks installation ${openFirst?'after':'before'} opening confirmation`,async()=>{
    const h=await updateHarness(readyUpdate);
    if(openFirst)clickUpdate(h,'install-update');
    h.api.snapshot=async()=>{throw Error('snapshot unavailable');};await h.refresh();
    assert.equal(h.el('install-update').disabled,true);
    assert.equal(h.el('check-updates').disabled,true);
    if(openFirst)h.el('confirm-ok').onclick();else clickUpdate(h,'install-update');
    await settle();
    assert.equal(h.calls.length,0);
    assert.equal(h.el('confirm-dialog').open,false);
    assert.match(h.el('toast').textContent,/尚未同步/);
  });
}

test('installation rejects a different synchronization epoch even after successful refresh',async()=>{
  const h=await updateHarness(readyUpdate);clickUpdate(h,'install-update');
  h.stop.listeners.click();await settle();
  assert.equal(h.el('connection-error').hidden,true);
  h.el('confirm-ok').onclick();await settle();
  assert.deepEqual(h.calls.map(call=>call.action),['network_stop']);
  assert.match(h.el('toast').textContent,/重新确认/);
});

test('pending bridge actions keep ready update buttons disabled even if a poll succeeds',async()=>{
  const h=await updateHarness(readyUpdate);
  const dispatch=h.api.dispatch;let complete;
  h.api.dispatch=async(action,payload)=>{const result=await dispatch(action,payload);await new Promise(resolve=>complete=()=>resolve(result));return result;};
  h.stop.listeners.click();await settle();await h.refresh();
  assert.equal(h.el('install-update').disabled,true);
  assert.equal(h.el('check-updates').disabled,true);
  clickUpdate(h,'install-update');clickUpdate(h,'check-updates');
  assert.equal(h.el('confirm-dialog').open,false);
  assert.deepEqual(h.calls.map(call=>call.action),['network_stop']);
  complete();await settle();
  assert.equal(h.el('install-update').disabled,false);
});

const updateDrafts={
  network:h=>{h.el('username').value='unsaved-user';h.el('network-form').listeners.input();},
  dorm:h=>{h.el('auto-interval').value='600';h.el('dorm-form').listeners.input();},
  source:h=>pickSource(h,'simulation'),
};
for(const [kind,edit] of Object.entries(updateDrafts)){
  for(const openFirst of [false,true]){
    test(`unsaved ${kind} draft blocks installation ${openFirst?'during':'before'} confirmation`,async()=>{
      const h=await updateHarness(readyUpdate);
      if(openFirst)clickUpdate(h,'install-update');
      edit(h);await h.refresh();
      if(openFirst)h.el('confirm-ok').onclick();else clickUpdate(h,'install-update');
      await settle();
      assert.equal(h.calls.length,0);
      assert.equal(h.el('confirm-dialog').open,false);
      assert.match(h.el('toast').textContent,/未保存.*先保存/);
      if(kind==='network')assert.equal(h.el('username').value,'unsaved-user');
      if(kind==='dorm')assert.equal(h.el('auto-interval').value,'600');
      if(kind==='source')assert.equal(h.el('location-source').value,'simulation');
    });
  }
}

test('launched means the wizard opened, not that the version is installed',async()=>{
  const h=await updateHarness({...readyUpdate,state:'launched',message:'已打开安装向导，请按向导完成安装，完成后重新打开程序。'});
  assert.equal(h.el('update-status').textContent,'已打开安装向导');
  assert.match(h.el('update-message').textContent,/按向导完成安装/);
  assert.doesNotMatch(h.el('update-status').textContent,/安装成功|更新成功|已安装/);
  assert.equal(h.el('update-current-version').textContent,'1.4.4');
  assert.equal(h.el('install-update').hidden,true);
  assert.equal(h.el('update-notice').hidden,true);
  assert.equal(h.calls.length,0);
});

test('release messages and version strings are rendered literally, never as cloud HTML',async()=>{
  const message='<img src=x onerror=alert(1)> 下载失败，请重新检查。';
  const version='<b>1.5.0</b>';
  const h=await updateHarness({state:'error',message,latest_version:version});
  assert.equal(h.el('update-message').textContent,message);
  assert.equal(h.el('update-latest-version').textContent,version);
  assert.ok(h.el('update-notice-text').textContent.includes(message));
  h.data.update={...updateFixture(readyUpdate),latest_version:version};await h.refresh();
  clickUpdate(h,'install-update');
  assert.ok(h.el('confirm-title').textContent.includes(version));
  assert.equal(h.el('update-notice-link').attributes.href,'#updates');
  h.el('confirm-cancel').onclick();
  assert.equal(h.calls.length,0);
});

/* Simulation map picker ------------------------------------------------------------------- */
const mapZoom=h=>vm.runInContext('mapState?mapState.zoom:null',h.context);
const mapCentre=h=>vm.runInContext('mapState?mapState.center:null',h.context);
const mapPicked=h=>vm.runInContext('mapState?mapState.picked:null',h.context);
async function openMap(h){
  assert.equal(h.el('open-map-picker').hidden,false);
  h.el('open-map-picker').onclick();
  await settle();await settle();
}
function clickMap(h,x,y){h.el('map-canvas').listeners.click({offsetX:x,offsetY:y});}

test('the picker stays closed until it is opened, and only in simulation mode',async()=>{
  const windows=harness('windows');await settle();
  assert.equal(windows.el('open-map-picker').hidden,true);
  windows.el('open-map-picker').onclick();await settle();await settle();
  assert.equal(windows.el('map-dialog').open,false);
  assert.equal(windows.calls.filter(c=>c.action.startsWith('simulation')).length,0);

  const h=harness('simulation');await settle();
  assert.equal(h.el('open-map-picker').hidden,false);
  assert.equal(h.el('map-dialog').open,false);
  await openMap(h);
  assert.equal(h.el('map-dialog').open,true);
  assert.equal(h.el('open-map-picker').hidden,true);
  assert.match(h.el('map-summary').textContent,/示例宿舍/);
  assert.equal(h.el('map-save').disabled,true);
  assert.match(h.el('map-distance-badge').textContent,/^已保存 · 距基准点 \d+ 米（范围内）$/);
});

test('clicking the map picks a point and saving sends those coordinates',async()=>{
  const h=harness('simulation');await settle();
  await openMap(h);
  clickMap(h,320,180);
  assert.ok(Math.abs(mapPicked(h).latitude-29.823940)<0.00005);
  assert.ok(Math.abs(mapPicked(h).longitude-106.422470)<0.00005);
  assert.equal(h.el('map-save').disabled,false);
  assert.match(h.el('map-distance-badge').textContent,/^待保存 · 距基准点 0 米（范围内）$/);
  h.el('map-point-name').value='宿舍楼下';
  h.el('map-save').onclick();await settle();await settle();
  const save=h.calls.find(c=>c.action==='simulation_point_save');
  assert.ok(save,'saving must reach the bridge');
  assert.ok(Math.abs(save.payload.latitude-29.823940)<0.00005);
  assert.ok(Math.abs(save.payload.longitude-106.422470)<0.00005);
  assert.equal(mapPicked(h),null);
  assert.match(h.el('map-distance-badge').textContent,/^已保存 · /);
  assert.equal(h.el('map-points').options.length,2);
});

test('a pick outside the allowed radius is labelled before it is saved',async()=>{
  const h=harness('simulation');await settle();
  h.map.reference.radius_m=50;
  await openMap(h);
  clickMap(h,320,100);
  assert.match(h.el('map-distance-badge').textContent,/待保存 · 距基准点 \d+ 米（超出范围）$/);
  assert.equal(h.el('map-save').disabled,false);
});

test('saving is impossible without a pick',async()=>{
  const h=harness('simulation');await settle();
  await openMap(h);
  h.el('map-save').onclick();await settle();
  assert.equal(h.calls.length,0);
});

test('zoom is clamped and recentring returns to the school point',async()=>{
  const h=harness('simulation');await settle();
  await openMap(h);
  for(let i=0;i<12;i++)h.el('map-zoom-out').onclick();
  assert.equal(mapZoom(h),13);
  for(let i=0;i<12;i++)h.el('map-zoom-in').onclick();
  assert.equal(mapZoom(h),19);
  h.el('map-canvas').listeners.keydown({key:'ArrowUp',shiftKey:true,preventDefault(){}});
  assert.ok(Math.abs(mapCentre(h).latitude-29.823940)>0.001);
  h.el('map-recenter').onclick();
  assert.ok(Math.abs(mapCentre(h).latitude-29.823940)<1e-9);
  assert.ok(Math.abs(mapCentre(h).longitude-106.422470)<1e-9);
});

test('the basemap can be switched off without losing the picker',async()=>{
  const h=harness('simulation');await settle();
  await openMap(h);
  h.el('map-tiles').checked=true;h.el('map-tiles').listeners.change();
  assert.equal(vm.runInContext('mapCredit(mapState)',h.context),'© OpenStreetMap contributors',
               'the map data keeps its credit, drawn on the canvas instead of a paragraph');
  h.el('map-tiles').checked=false;h.el('map-tiles').listeners.change();
  assert.equal(vm.runInContext('mapCredit(mapState)',h.context),'',
               'no basemap means no third-party data to credit');
  assert.equal(h.el('map-dialog').open,true);
  // 冗长的底图说明段落已删除，只留画布上的一行署名。
  const markup=fs.readFileSync(path.join(__dirname,'../desktop_ui/index.html'),'utf8');
  assert.doesNotMatch(markup,/map-attribution/);
});

test('the picker still works before the school publishes a point',async()=>{
  const h=harness('simulation');await settle();
  h.map.reference=null;
  await openMap(h);
  assert.match(h.el('map-summary').textContent,/尚未查询今日任务/);
  assert.match(h.el('map-distance-badge').textContent,/^已保存 · 距基准点未知$/);
  clickMap(h,320,180);
  assert.equal(h.el('map-save').disabled,false);
  assert.match(h.el('map-distance-badge').textContent,/^待保存 · 距基准点未知$/);
  h.el('map-save').onclick();await settle();await settle();
  assert.ok(h.calls.some(c=>c.action==='simulation_point_save'));
});

test('the basemap host is allowed by the content security policy',async()=>{
  const html=fs.readFileSync(path.join(__dirname,'../desktop_ui/index.html'),'utf8');
  const csp=html.match(/Content-Security-Policy" content="([^"]+)"/)[1];
  const imagePolicy=csp.split(';').map(part=>part.trim()).find(part=>part.startsWith('img-src'));
  assert.ok(imagePolicy,'the page must state an img-src policy');
  assert.match(imagePolicy,/'self'/, 'same-origin images stay allowed');
  const h=harness('simulation');await settle();
  await openMap(h);
  const host=new URL(h.map.tile_url.replace('{z}','17').replace('{x}','1').replace('{y}','1')).host;
  assert.ok(imagePolicy.includes(host),'img-src must name the basemap host '+host);
  assert.equal((csp.match(/https:\/\//g)||[]).length,1,'the picker adds exactly one remote origin');
});

test('dragging pans the map and never drops a marker',async()=>{
  const h=harness('simulation');await settle();
  await openMap(h);
  const canvas=h.el('map-canvas'),before=mapCentre(h);
  canvas.listeners.pointerdown({offsetX:360,offsetY:200,pointerId:1});
  canvas.listeners.pointermove({offsetX:300,offsetY:200,pointerId:1});
  canvas.listeners.pointerup({offsetX:300,offsetY:200,pointerId:1});
  const after=mapCentre(h);
  assert.ok(after.longitude>before.longitude,'dragging left moves the view east');
  assert.ok(after.latitude-before.latitude<1e-9,'a horizontal drag does not tilt the view');
  assert.equal(mapPicked(h),null,'a drag is not a pick');
  canvas.listeners.click({offsetX:300,offsetY:200});
  assert.equal(mapPicked(h),null,'the click that ends a drag is swallowed');
  canvas.listeners.click({offsetX:320,offsetY:180});
  assert.ok(mapPicked(h),'a plain click still picks');
});

test('the wheel zooms and stays inside the allowed range',async()=>{
  const h=harness('simulation');await settle();
  await openMap(h);
  const canvas=h.el('map-canvas'),start=mapZoom(h);
  canvas.listeners.wheel({deltaY:-120,preventDefault(){}});
  assert.equal(mapZoom(h),start+1);
  for(let step=0;step<12;step++)canvas.listeners.wheel({deltaY:120,preventDefault(){}});
  assert.equal(mapZoom(h),13,'zooming out stops at the minimum');
  for(let step=0;step<12;step++)canvas.listeners.wheel({deltaY:-120,preventDefault(){}});
  assert.equal(mapZoom(h),19,'zooming in stops at the maximum');
});

test('saved points can be listed, switched, renamed and deleted',async()=>{
  const h=harness('simulation');await settle();
  await openMap(h);
  const select=h.el('map-points');
  assert.deepEqual(select.options.map(option=>option.value),['p1']);
  assert.match(select.options[0].textContent,/^本机采样样本 · \d+ 米$/);
  assert.equal(select.value,'p1');
  assert.match(h.el('map-point-state').textContent,/当前生效：本机采样样本/);
  assert.equal(h.el('map-point-name').value,'本机采样样本');

  clickMap(h,360,200);
  h.el('map-point-name').value='图书馆北门';
  h.el('map-save').onclick();await settle();await settle();
  const save=h.calls.find(call=>call.action==='simulation_point_save');
  assert.equal(save.payload.name,'图书馆北门','the name travels with the pick');
  assert.equal(h.el('map-points').options.length,2);
  assert.match(h.el('map-point-state').textContent,/图书馆北门/);

  h.el('map-points').value='p1';
  h.el('map-points').listeners.change();await settle();await settle();
  assert.ok(h.calls.some(call=>call.action==='simulation_point_select'&&call.payload.id==='p1'),
            'choosing from the dropdown switches the active point');
  assert.equal(h.el('map-points').value,'p1');
  assert.equal(h.el('map-point-name').value,'本机采样样本','the box follows the switched point');

  h.el('map-point-name').value='原始采样';
  h.el('map-point-rename').onclick();await settle();await settle();
  const rename=h.calls.find(call=>call.action==='simulation_point_rename');
  assert.equal(rename.payload.id,'p1');
  assert.equal(rename.payload.name,'原始采样');
  assert.match(h.el('map-point-state').textContent,/原始采样/);

  h.el('map-point-delete').onclick();
  assert.match(h.el('confirm-title').textContent,/删除选点「原始采样」/);
  h.el('confirm-ok').onclick();await settle();await settle();
  assert.ok(h.calls.some(call=>call.action==='simulation_point_delete'&&call.payload.id==='p1'));
  // Deleting the active point switches to the remaining one instead of losing the position.
  assert.deepEqual(h.el('map-points').options.map(option=>option.value),['p2']);
  assert.equal(h.el('map-points').value,'p2');
  assert.match(h.el('map-point-state').textContent,/当前生效：图书馆北门/);
});

test('map markers carry the point name instead of a generic label',async()=>{
  const h=harness('simulation');await settle();
  await openMap(h);
  assert.equal(vm.runInContext('mapSavedLabel(mapState)',h.context),'本机采样样本');
  clickMap(h,320,180);
  assert.equal(vm.runInContext('mapPendingLabel(mapState)',h.context),'待保存：本机采样样本',
               'a pick keeps the active name, so the marker says which point it would move');
  h.el('map-point-name').value='杏园三舍';
  assert.equal(vm.runInContext('mapPendingLabel(mapState)',h.context),'待保存：杏园三舍',
               'the pending marker shows the name being typed');
  h.el('map-save').onclick();await settle();await settle();
  assert.equal(vm.runInContext('mapSavedLabel(mapState)',h.context),'杏园三舍');
});

test('the name box survives background redraws while it is being edited',async()=>{
  // Tile loads redraw the map constantly; the box used to be rewritten from the active point on
  // every redraw, which silently replaced whatever the user was typing.
  const h=harness('simulation');await settle();
  await openMap(h);
  h.el('map-point-name').value='宿舍东门';
  vm.runInContext('renderMap()',h.context);      // what a tile arriving mid-typing does
  vm.runInContext('renderMap()',h.context);
  assert.equal(h.el('map-point-name').value,'宿舍东门','typing must not be wiped');
  h.el('map-point-rename').onclick();await settle();await settle();
  const rename=h.calls.find(call=>call.action==='simulation_point_rename');
  assert.equal(rename.payload.name,'宿舍东门','the typed name is what gets renamed');
  assert.match(h.el('map-point-state').textContent,/当前生效：宿舍东门/);
  assert.equal(h.el('map-point-name').value,'宿舍东门');
});

test('a sample that is not a saved point is offered as itself',async()=>{
  const h=harness('simulation');await settle();
  h.map.points=[];h.map.active_id='';
  await openMap(h);
  assert.equal(h.el('map-points').options.length,1);
  assert.match(h.el('map-points').options[0].textContent,/当前样本（未命名）/);
  assert.match(h.el('map-point-state').textContent,/当前生效：未命名的样本/);
  assert.equal(h.el('map-point-rename').disabled,true);
  assert.equal(h.el('map-point-delete').disabled,true);
});

test('closing the map or leaving simulation mode hides the picker',async()=>{
  const h=harness('simulation');await settle();
  await openMap(h);
  h.el('map-close').onclick();
  assert.equal(h.el('map-dialog').open,false);
  assert.equal(h.el('open-map-picker').hidden,false);
  await openMap(h);
  h.data.dorm.settings.location_source='windows';
  await h.refresh();
  assert.equal(h.el('map-dialog').open,false);
  assert.equal(h.el('open-map-picker').hidden,true);
});
