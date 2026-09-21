'use strict';
const $ = id => document.getElementById(id);
const titles = {overview:'今日概览',network:'校园网',dorm:'寝室打卡',records:'运行记录'};
let state = null, logType = 'network', pending = false, refreshTask = null, epoch = 0, timer;
const dirty = {network:false, dorm:false};
const revisions = {network:0, dorm:0};
let api;

function notify(message, error=false) {
  clearTimeout(timer);
  $('toast').textContent = message;
  $('toast').classList.toggle('error', error);
  $('toast').hidden = false;
  timer = setTimeout(() => {$('toast').hidden = true;}, error ? 7000 : 4200);
}
function navigate() {
  const page = location.hash.slice(1) in titles ? location.hash.slice(1) : 'overview';
  for (const id of Object.keys(titles)) $(id).hidden = id !== page;
  document.querySelectorAll('.nav-link').forEach(link => {
    const active = link.hash === '#' + page;
    link.classList.toggle('active', active);
    if(active) link.setAttribute('aria-current','page'); else link.removeAttribute('aria-current');
  });
  $('page-context').textContent = titles[page];
  window.scrollTo(0,0);
}
window.addEventListener('hashchange', navigate);
window.desktopNavigate = page => { if (page in titles) location.hash = page; };
navigate();
$('date-label').textContent = new Intl.DateTimeFormat('zh-CN',{month:'long',day:'numeric',weekday:'long'}).format(new Date());

function badge(id, text, tone='') {$(id).textContent=text;$(id).className='badge'+(tone ? ' '+tone : '');}
function markDirty(kind, value) {
  dirty[kind] = value;
  $(kind+'-save-state').textContent = value ? '有未保存的修改' : '设置已同步';
  $(kind+'-save-state').classList.toggle('dirty',value);
}
['network','dorm'].forEach(kind=>$(kind+'-form').addEventListener('input',()=>{revisions[kind]++;markDirty(kind,true);}));

function render() {
  if(!state) return;
  const n=state.network,d=state.dorm;
  $('preview-tag').hidden = !state.preview;
  $('preview-tag').textContent = state.location_diagnostic ? '真实定位诊断 · 其他功能为演示' : '演示预览';
  const locationState=state.location||{state:'idle',message:'点击按钮授权并检测定位，不会提交打卡。',busy:false};
  $('location-message').textContent=locationState.message;
  $('location-message').classList.toggle('location-success',locationState.state==='ready');
  $('location-message').classList.toggle('location-error',!['idle','checking','ready'].includes(locationState.state));
  $('authorize-location').disabled=pending||locationState.busy;
  $('authorize-location').textContent=locationState.busy?'正在授权 / 检测…':'授权并检测定位';
  $('location-checked').textContent=locationState.checked ? '最近检测 · '+locationState.checked : '仅检测定位，不会提交打卡';
  $('network-status').textContent=n.message;
  $('network-summary').textContent=n.message;
  $('checked-label').textContent=n.checked ? '最近检测 · '+n.checked : '尚未执行连接检测';
  const names={online:'网络已连接',offline:'需要重新连接',checking:'正在检测',stopped:'尚未检测',error:'需要检查设置'};
  $('network-headline').textContent=names[n.state] || '等待检测';
  badge('network-badge',n.state==='online'?'连接正常':n.state==='checking'?'检测中':n.state==='offline'?'未连接':n.state==='error'?'检测异常':'等待检测',n.state==='online'?'success':['offline','error'].includes(n.state)?'warning':'');
  const dormNames={signed:'今日已完成',ready:'今日待打卡',idle:'查看今日安排',logged_in:'已登录，待查询',login_required:'需要学校登录',uncertain:'结果待确认',location_required:'需要检查定位',expired:'任务已过期',waiting:'等待学校时段',no_task:'今天暂无任务',error:'需要检查',network_error:'网络异常',cancelled:'操作已取消',busy:'处理中'};
  const dormLabel=d.busy?'正在处理…':(dormNames[d.state]||'请查看任务状态');
  $('dorm-headline').textContent=dormLabel;
  $('dorm-summary').textContent=d.message;
  const tone=d.state==='signed'?'success':['uncertain','error','network_error','location_required','login_required'].includes(d.state)?'warning':'';
  badge('dorm-badge',dormLabel,tone);badge('task-badge',dormLabel,tone);
  $('monitor-summary').textContent=n.startup?'系统代理运行中':n.monitoring?'正在后台检测':'未开启';
  $('auto-summary').textContent=d.settings.enabled ? d.settings.start+'–'+d.settings.end : '未开启';
  badge('monitor-badge',n.startup?'系统代理管理':n.monitoring?'运行中':'未开启',n.monitoring?'success':'');
  $('agent-hint').textContent=n.startup?'由系统代理持续运行；停用请取消开机自启动并保存。':'开启前请先保存设置。';
  $('overview-attention').textContent=n.state!=='online'?'从一次连接检测开始':d.state==='signed'?'今天的安排，已妥当':d.state==='ready'?'别忘了今晚的寝室打卡':'看看今天的寝室安排';
  $('task-title').textContent=d.task?d.task.title:'先看看今天的安排';
  $('task-message').textContent=d.busy?'正在处理，请稍候；学校登录可能需要在浏览器中完成。':d.message;
  if(d.state==='location_required'&&locationState.state==='ready'&&!d.busy)$('task-message').textContent='定位检测已通过。请查询今日任务，刷新任务状态后再提交。';
  $('task-details').hidden=!d.task;
  if(d.task){$('task-date').textContent=d.task.date;$('task-time').textContent=d.task.start+'–'+d.task.end;$('task-address').textContent=d.task.address||'以学校任务为准';}
  $('schedule').textContent=d.schedule;
  if(!dirty.network){$('username').value=n.username;$('interval').value=n.interval;$('startup').checked=n.startup;}
  if(!dirty.dorm){$('auto-enabled').checked=d.settings.enabled;$('auto-start').value=d.settings.start;$('auto-end').value=d.settings.end;$('auto-interval').value=d.settings.interval;}
  $('password-hint').textContent=n.has_password?'已保存加密密码；留空保存即可保留。':'尚未保存密码，请填写后保存。';
  document.querySelectorAll('[data-action="network_check"]').forEach(b=>b.disabled=pending||n.busy||(!n.startup&&n.monitoring));
  document.querySelectorAll('[data-action="network_start"]').forEach(b=>b.disabled=pending||n.busy||n.monitoring);
  document.querySelectorAll('[data-action="network_stop"]').forEach(b=>b.disabled=pending||!n.monitoring||n.startup);
  document.querySelectorAll('[data-action="dorm_login"],[data-action="dorm_query"]').forEach(b=>b.disabled=pending||d.busy);
  $('submit-dorm').disabled=pending||d.busy||!['ready','uncertain'].includes(d.state);
  $('submit-dorm').firstChild.textContent=d.state==='uncertain'?'回查提交结果 ':'提交今日打卡 ';
  $('cancel-dorm').disabled=pending||!d.busy;
  $('logout-dorm').disabled=pending||d.busy;
  document.querySelectorAll('button[type="submit"]').forEach(b=>b.disabled=pending||(b.closest('form').id==='dorm-form'&&d.busy));
  renderLogs();
}
function renderLogs(){if(!state)return;const text=state.logs[logType]||'';$('log-output').textContent=text;$('log-output').hidden=!text.trim();$('empty-records').hidden=!!text.trim();}
async function refresh(){
  if(!api)return;
  if(refreshTask)return refreshTask;
  const started=epoch;
  refreshTask=(async()=>{try{const next=await api.snapshot();if(started!==epoch)return;state=next;$('connection-error').hidden=true;render();}catch(error){$('connection-error').hidden=false;}})();
  try{await refreshTask;}finally{refreshTask=null;}
}
async function act(action,payload={}){
  if(!api||pending)return false;
  pending=true;epoch++;render();
  try{const result=await api.dispatch(action,payload);notify(result.message,!result.ok);return result.ok;}catch(error){notify('操作未完成，请检查后台连接后重试。',true);return false;}finally{pending=false;epoch++;if(refreshTask)await refreshTask;await refresh();render();}
}
document.querySelectorAll('[data-action]').forEach(button=>button.addEventListener('click',()=>{
  const action=button.dataset.action;
  if(['network_check','network_start'].includes(action)&&dirty.network){notify('校园网设置尚未保存，请先保存再检测。',true);location.hash='network';return;}
  act(action);
}));
$('network-form').addEventListener('submit',async event=>{
  event.preventDefault();
  const payload={username:$('username').value,password:$('password').value,interval:Number($('interval').value),startup:$('startup').checked};
  const revision=revisions.network;
  if(await act('network_save',payload)){if(revision===revisions.network){markDirty('network',false);$('password').value='';}render();}
});
$('dorm-form').addEventListener('submit',async event=>{
  event.preventDefault();
  const payload={enabled:$('auto-enabled').checked,start:$('auto-start').value,end:$('auto-end').value,interval:Number($('auto-interval').value)};
  if(payload.start>=payload.end){notify('结束时间必须晚于开始时间，暂不支持跨日时段。',true);return;}
  const revision=revisions.dorm;
  if(await act('dorm_save',payload)){if(revision===revisions.dorm)markDirty('dorm',false);render();}
});
$('toggle-password').addEventListener('click',()=>{const show=$('password').type==='password';$('password').type=show?'text':'password';$('toggle-password').setAttribute('aria-label',show?'隐藏密码':'显示密码');$('toggle-password').setAttribute('aria-pressed',String(show));});
let confirmation=null,previousFocus=null;
function confirmAction(title,text,action){previousFocus=document.activeElement;$('confirm-title').textContent=title;$('confirm-text').textContent=text;confirmation=action;$('confirm-dialog').showModal();$('confirm-cancel').focus();}
$('confirm-cancel').onclick=()=>$('confirm-dialog').close();
$('confirm-dialog').addEventListener('close',()=>{confirmation=null;previousFocus?.focus();});
$('confirm-ok').onclick=()=>{const action=confirmation;$('confirm-dialog').close();if(action)action();};
$('submit-dorm').onclick=()=>confirmAction(state.dorm.state==='uncertain'?'回查提交结果？':'提交今日打卡？','将使用 Windows 实时定位，向学校发送登录凭据、任务字段和位置。提交后会回查学校结果。',()=>act('dorm_submit'));
$('logout-dorm').onclick=()=>confirmAction('清除学校登录凭据？','这会同时关闭自动打卡。校园网账号不受影响，下次打卡需要重新登录学校账号。',async()=>{if(await act('dorm_logout')){markDirty('dorm',false);render();}});
$('quit').onclick=()=>confirmAction('退出 youziauth？','退出将停止本程序的后台检测和自动打卡。已启用的系统认证代理仍会运行。',()=>act('quit'));
$('refresh-records').onclick=async()=>{await refresh();if($('connection-error').hidden)notify('运行记录已刷新');};
document.querySelectorAll('[data-log]').forEach(button=>button.onclick=()=>{logType=button.dataset.log;document.querySelectorAll('[data-log]').forEach(b=>{b.classList.toggle('selected',b===button);b.setAttribute('aria-pressed',String(b===button));});renderLogs();});

function connect(){if(api||!window.pywebview?.api)return;api=window.pywebview.api;refresh();}
window.addEventListener('pywebviewready',connect);
connect();
// Only the isolated preview server sets this query. Native production uses the bridge only.
if(new URLSearchParams(location.search).get('preview')==='1'){
  api={snapshot:async()=>{const r=await fetch('/api/state');if(!r.ok)throw Error();return r.json();},dispatch:async(action,payload)=>{const r=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,payload})});if(!r.ok)throw Error();return r.json();}};
  refresh();
}
setInterval(refresh,1500);
setTimeout(()=>{if(!api)$('connection-error').hidden=false;},6000);
