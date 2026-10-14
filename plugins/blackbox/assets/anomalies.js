'use strict';
const el = id => document.getElementById(id);
const auth = new URLSearchParams(location.hash.slice(1)).get('token') || '';
let current = null, openGeneration = 0, listGeneration = 0, captureGeneration = 0;
let exportPreview=null, exportGeneration=0, downloadingPreview=null, savingPreview=null;
let nextBefore = null, snapshotSeq = null, activeFilters = '', loading = false;
let view = 'list', listScroll = 0, listFocus = null, listHelpOpen = false, detailRoute = null;
let contextGeneration = 0, selectionGeneration = 0;
const projects = new Map(), sessionGroups = new Map(), sessionNames = new Map(), sessionViews = new Map();
const sessionStates = new Map();
let sessionScope='active', sessionAction=null, actionGeneration=0, actionBusy=false, typeGeneration=0, availableTypes=[];
let selectedSession = null, preferenceQueue = Promise.resolve(), mobileReturnScroll = 0;
let unfilteredTotal=null, workspaceHasAnomalies=null, selectedProject=null;
let onboardingStatus=null,contextsReady=false,contextsEmpty=false,connectFocus=null;
let pendingListReturn = null, listScrollPending = false;
let allMatchingSelected=false, matchingTotal=0, selectingAnomalies=false, selectedAnomalies=new Set(), excludedAnomalies=new Set(), cleanupProjects=[], cleanupPlan=null, cleanupBusy=false, cleanupToken=null, cleanupTrigger=null, cleanupGeneration=0;
const baseHash = '#'+new URLSearchParams({token:auth}).toString();
const wideQuery=window.matchMedia?.('(min-width: 1000px)');
function wideLayout() { return !!wideQuery?.matches; }
function listPosition() { return wideLayout()?el('list-view').scrollTop:window.scrollY; }
function scrollList(value) { if(wideLayout()) el('list-view').scrollTop=value; else window.scrollTo(0,value); }
function syncLayout() {
  el('list-view').hidden=view==='detail'&&!wideLayout();
  el('detail-view').hidden=view!=='detail'&&!wideLayout();
  el('detail-empty').hidden=view==='detail';
  el('detail-toolbar').hidden=view!=='detail';
  el('detail-context').hidden=view!=='detail'||!current;
  if(view!=='detail') {el('detail').hidden=true;el('retry').hidden=true;el('detail-status').textContent='';}
  syncOnboarding();
}
wideQuery?.addEventListener('change',()=>{syncLayout();scrollList(listScroll);});
function say(message) { el('status').textContent = message; if(el('help').open)el('help-action-status').textContent=message; }
function dateText(value, now=new Date(), seconds=true) {
  const date=new Date(value);
  if(!value||!Number.isFinite(date.getTime()))return '时间未知';
  const yesterday=new Date(now.getFullYear(),now.getMonth(),now.getDate()-1);
  const day=date.toDateString()===now.toDateString()?'今天':date.toDateString()===yesterday.toDateString()?'昨天':
    `${date.getFullYear()===now.getFullYear()?'':date.getFullYear()+'年'}${date.getMonth()+1}月${date.getDate()}日`;
  const time=date.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',...(seconds?{second:'2-digit'}:{}),hourCycle:'h23'});
  return `${day} ${time}`;
}
function absoluteDateText(value) {
  const date=new Date(value);
  if(!value||!Number.isFinite(date.getTime()))return '时间未知';
  const day=date.toLocaleDateString('zh-CN',{year:'numeric',month:'long',day:'numeric'});
  const time=date.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'});
  const zone=new Intl.DateTimeFormat('zh-CN',{timeZoneName:'shortOffset'}).formatToParts(date).find(p=>p.type==='timeZoneName')?.value || '';
  return `${day} ${time}${zone?'（'+zone+'）':''}`;
}
function refreshTimeLabels() {
  if(current)refreshDetailTime();
  for(const node of document.querySelectorAll?.('.capture-project-receipt[data-received]')||[])node.textContent=captureReceiptText({captured:node.dataset.received});
  const cards=new Set([...el('records').children,...[...sessionViews.values()].flatMap(v=>v.cards)]);
  for(const card of cards)for(const node of card.children)for(const child of node.children||[])if(child.dataset?.captured)child.textContent=dateText(child.dataset.captured);
}
function scheduleTimeLabels() {setTimeout(()=>{refreshTimeLabels();scheduleTimeLabels();},30000);}
scheduleTimeLabels();window.addEventListener('focus',refreshTimeLabels);window.addEventListener('pageshow',refreshTimeLabels);
document.addEventListener?.('visibilitychange',refreshTimeLabels);
function projectText(path) { return projects.get(path)?.name || '未关联项目 · '+(path.split(/[\\/]/).filter(Boolean).pop() || path); }
function sessionText(session) {
  const name=sessionNames.get(session?.id);
  if(name) return name;
  const label={unlinked:'未关联记录',untitled:'无标题会话',unavailable:'会话信息暂不可用'}[sessionStates.get(session?.id)] || '正在读取会话信息…';
  const stamp=session?.last_captured ? dateText(session.last_captured) : '';
  return [label,stamp,session?.id?.slice(0,8)].filter(Boolean).join(' · ');
}
function refreshDetailTime() {
  el('meta').textContent=`记录于 ${dateText(current.captured)}`;
  el('meta').title=absoluteDateText(current.captured);
}
function updateHeadings() {
  if(selectedSession) {
    el('list-heading').textContent='异常记录';
    el('session-description').textContent=sessionText(selectedSession);
    el('session-description').hidden=false;
  }
  if(current) {
    el('detail-context').textContent=`${projectText(current.project)} / ${sessionText({id:current.investigation})}`;
    el('detail-context').hidden=false;
    refreshDetailTime();
  }
}
function syncFilterState() {
  el('clear').hidden=el('time').value==='all'&&!el('anomaly-type').value;
}
function syncRecordCount() {
  const count=el('records').children.length;
  el('record-count').textContent=snapshotSeq==null?'':`已显示 ${count} / ${unfilteredTotal===null?'—':unfilteredTotal} 条 · 按保存时间排列`;
}
function rememberSession(id) {
  // Serialize writes so an older request can never overwrite a newer choice.
  preferenceQueue=preferenceQueue.catch(()=>{}).then(()=>request('/api/reader-selection','POST',{investigation:id})).catch(()=>{});
}
function saveSessionView() {
  if(loading || !selectedSession || snapshotSeq===null || snapshotSeq===undefined) return;
  sessionViews.delete(selectedSession.id);
  sessionViews.set(selectedSession.id,{
    matchingTotal,cards:Array.from(el('records').children),nextBefore,snapshotSeq,activeFilters,
    scroll:el('workspace').dataset.stage==='navigation'?mobileReturnScroll:wideLayout()?listPosition():view==='list'&&!listScrollPending?window.scrollY:listScroll,focus:listFocus,
    controls:Object.fromEntries(['time','anomaly-type','start','end'].map(id=>[id,el(id).value])),
    status:el('list-status').textContent,more:el('more').textContent
  });
  if(sessionViews.size>10) sessionViews.delete(sessionViews.keys().next().value);
}
function resetFilters() {
  for(const id of ['anomaly-type','start','end']) el(id).value='';
  el('time').value='all'; el('date-range').hidden=true;
  syncTimePicker();renderTypes();
}
const timeOptions={all:'全部时间',today:'今天',week:'近七天',custom:'自定义日期'};
function syncTimePicker() {
  syncDates();closeCalendar(false);
  syncFilterState();
  el('time-label').textContent=timeOptions[el('time').value] || timeOptions.all;
  for(const key of Object.keys(timeOptions)) el('time-'+key).setAttribute('aria-pressed',String(key===el('time').value));
}
function selectSession(project,session,{preserveView=false,remember=true}={}) {
  if(!el('cleanup-dialog').hidden&&!cleanupBusy)closeCleanup();
  // Let an already requested Back finish before replacing its current history entry.
  if(pendingListReturn) return pendingListReturn.promise.then(()=>selectSession(project,session,{preserveView,remember}));
  project=session.project || project;
  const same=selectedSession?.id===session.id;
  if(!same) {cancelSessionAction();++typeGeneration;availableTypes=[];unfilteredTotal=null;renderTypes();}
  const keepDetail=wideLayout() && view==='detail';
  const fromNavigation=el('workspace').dataset.stage==='navigation';
  if(!same) {saveSessionView();selectingAnomalies=false;resetSelection();}
  selectedProject=project;selectedSession={...session,project}; el('project').value=project; el('session').value=session.id;
  el('filters').hidden=false;
  const selectedGroup=[...sessionGroups.values()].find(g=>g.paths?.includes(project)) || sessionGroups.get(project);
  if(selectedGroup) {
    selectedGroup.details.open=true;
    const position=navigationSessions(selectedGroup).findIndex(s=>s.id===session.id);
    if(position>=5) selectedGroup.expanded=true;
    arrangeSessions(selectedGroup);
  }
  ++selectionGeneration;
  if(remember) rememberSession(session.id);
  for(const group of sessionGroups.values()) for(const [id,nodes] of group.buttons) nodes.button.setAttribute('aria-current',id===session.id?'page':'false');
  el('session-actions').hidden=false;el('archive-session').textContent=session.archived?'恢复会话':'归档会话';
  updateHeadings(); el('nav-return').hidden=false; el('workspace').dataset.stage='reading';
  if(same && !preserveView) {
    if(view==='detail'&&!keepDetail) backToList();
    else if(fromNavigation) requestAnimationFrame(()=>window.scrollTo(0,mobileReturnScroll));
    return Promise.resolve();
  }
  ++listGeneration; loading=false; el('list-view').setAttribute('aria-busy','false'); el('more').disabled=false;
  const saved=sessionViews.get(session.id);
  listScroll=saved?.scroll || 0; listFocus=saved?.focus || null;
  if(!preserveView&&!keepDetail) {showList();history.replaceState({view:'list'},'',baseHash);}
  if(keepDetail) {
    const generation=selectionGeneration, position=listScroll;
    requestAnimationFrame(()=>{if(generation===selectionGeneration)scrollList(position);});
  }
  resetFilters();
  if(saved) {
    for(const [id,value] of Object.entries(saved.controls)) el(id).value=value;
    syncTimePicker();
    el('date-range').hidden=el('time').value!=='custom';
    el('records').replaceChildren(...saved.cards);
    for(const card of el('records').children)card.setAttribute('aria-current',String(card.dataset.recordId===String(current?.id)));
    ({nextBefore,snapshotSeq,activeFilters,matchingTotal}=saved);syncSelection();
    syncRecordCount();
    el('list-status').textContent=saved.status;
    el('more').textContent=saved.more; el('more').hidden=nextBefore==null;
    el('list-empty').hidden=saved.cards.length>0;
    refreshTypes();return Promise.resolve();
  }
  nextBefore=null; snapshotSeq=null; activeFilters='';
  syncRecordCount();
  el('records').replaceChildren();el('more').hidden=true;el('list-empty').hidden=true;
  el('list-status').textContent='正在读取这个会话的记录…';
  return load();
}
function navigationSessions(group) {
  return group.sessions.filter(s=>sessionStates.get(s.id)!=='unlinked' && !!s.archived===(sessionScope==='archived'));
}
function arrangeSessions(group) {
  const regular=[],focused=document.activeElement;
  for(const session of group.sessions) {
    const nodes=group.buttons.get(session.id);
    if(!nodes) continue;
    nodes.label.textContent=sessionText(session);nodes.button.title=sessionText(session);
    nodes.button.hidden=sessionStates.get(session.id)==='unlinked' || !!session.archived!==(sessionScope==='archived');
    if(!nodes.button.hidden) regular.push(nodes.button);
  }
  const visible=group.expanded?regular:regular.slice(0,5);
  group.body.replaceChildren(...visible);
  group.more.hidden=regular.length<=5;
  group.more.textContent=group.expanded?'收起':'展开显示';
  group.more.setAttribute('aria-expanded',String(group.expanded));
  if(visible.includes(focused)) focused.focus({preventScroll:true});
  else if([...group.buttons.values()].some(n=>n.button===focused)) {
    (visible[0] || group.summary).focus({preventScroll:true});
  }
}
async function loadNames(group,generation) {
  for(let index=0;index<group.sessions.length;index+=20) {
    if(group.generation!==generation) return;
    const batch=group.sessions.slice(index,index+20),query=new URLSearchParams();
    for(const session of batch) query.append('investigation',session.id);
    try {
      const data=await (await request('/api/session-names?'+query)).json();
      if(group.generation!==generation) return;
      for(const session of batch) {
        const name=data.names?.[session.id];
        if(typeof name==='string' && name.trim()) sessionNames.set(session.id,name);
        else sessionNames.delete(session.id);
        const state=data.states?.[session.id] || (name?'named':'unavailable');
        sessionStates.set(session.id,state);
      }
      arrangeSessions(group);updateHeadings();
    } catch(_) {
      if(group.generation!==generation) return;
      // Later batches will not be requested after this failure; none may remain pending.
      for(const session of group.sessions.slice(index)) {sessionNames.delete(session.id);sessionStates.set(session.id,'unavailable');}
      arrangeSessions(group);updateHeadings();break;
    }
  }
  if(group.generation!==generation) return;
  const visible=navigationSessions(group);
  const unavailable=visible.some(s=>sessionStates.get(s.id)==='unavailable');
  group.notice.textContent=unavailable?'宿主会话信息暂不可用，仍可读取本地记录。':visible.length?'':'暂无可显示的会话。';
  if(view!=='detail' && group.sessions.some(s=>s.id===selectedSession?.id) && !visible.some(s=>s.id===selectedSession?.id)) {
    if(visible.length) await selectSession(group.paths[0],visible[0]);
    else {
      clearSession();
    }
  }
  group.details.hidden=false;
}
async function populateProject(path,force=false) {
  const group=sessionGroups.get(path);
  if(!group) return [];
  if(group.pending && !force) return group.pending;
  if(group.loaded && !force) return group.sessions;
  const generation=++group.generation;
  group.notice.textContent='正在读取会话…';
  const pending=(async()=>{
    try {
      const data=await (await request('/api/anomaly-contexts?'+new URLSearchParams({project_path:path}))).json();
      if(group.generation!==generation || sessionGroups.get(path)!==group) return [];
      group.sessions=data.sessions;group.loaded=true;group.body.replaceChildren();group.buttons.clear();
      for(const session of group.sessions) {
        const button=document.createElement('button');button.className='session-link';button.hidden=false;button.dataset.sessionId=session.id;
        button.setAttribute('aria-current',selectedSession?.id===session.id?'page':'false');
        const label=block(button,'span',sessionText(session));label.className='session-name';
        button.title=sessionText(session);
        button.addEventListener('click',()=>selectSession(path,session).catch(e=>say(e.message)));
        group.buttons.set(session.id,{button,label});
      }
      arrangeSessions(group);
      group.notice.textContent=group.sessions.length?'':'这个项目尚无已保存的会话。';
      loadNames(group,generation).catch(error=>{if(group.generation===generation)say(error.message);});
      return group.sessions;
    } catch(error) {
      if(group.generation===generation) group.notice.textContent='会话未能加载，请刷新项目重试。';
      throw error;
    } finally {if(group.generation===generation) group.pending=null;}
  })();
  group.pending=pending;return pending;
}
async function selectProject(path) {
  const generation=++selectionGeneration;
  await populateProject(path);
  if(generation!==selectionGeneration)return;
  const group=sessionGroups.get(path);
  if(!group)return;
  const sessions=navigationSessions(group);
  const session=sessions.find(s=>s.id===selectedSession?.id)||sessions[0];
  if(session)return selectSession(path,session);
  clearSession({preserveDetail:wideLayout()&&view==='detail'});selectedProject=path;
  syncProjectList();
  syncOnboarding();
}
function syncProjectList() {
  if(workspaceHasAnomalies===true){
    el('list-heading').textContent='异常记录';el('session-description').textContent=projectText(selectedProject);
    el('list-status').textContent='0 条异常记录';el('list-empty').hidden=false;
  }
}
function bindProjectDisclosure(details,summary) {
  let animation=null, fading=null, target=null;
  summary.addEventListener('click',event=>{
    if(!details.animate || window.matchMedia('(prefers-reduced-motion: reduce)').matches)return;
    event.preventDefault();
    const opening=target===null?!details.open:!target;
    const start=details.getBoundingClientRect().height;
    if(animation){animation.onfinish=null;animation.cancel();}
    fading?.cancel();
    target=opening;details.dataset.closing=String(!opening);
    details.open=true;
    const end=opening?details.getBoundingClientRect().height:summary.getBoundingClientRect().height;
    animation=details.animate([{height:start+'px',overflow:'hidden'},{height:end+'px',overflow:'hidden'}],{duration:180,easing:'cubic-bezier(.2,0,0,1)'});
    const body=details.querySelector('.project-sessions');
    fading=body?.animate([{opacity:opening?.35:1},{opacity:opening?1:0}],{duration:180,easing:'ease-out'});
    animation.onfinish=()=>{details.open=opening;delete details.dataset.closing;animation=null;target=null;fading?.cancel();fading=null;};
  });
}
async function contexts(preferred=null) {
  const generation=++contextGeneration, selectionAtStart=selectionGeneration;
  el('context-status').textContent='';
  try {
    const data=await (await request('/api/anomaly-contexts')).json();
    if(generation!==contextGeneration) return;
    if(typeof data.workspace_has_anomalies==='boolean')workspaceHasAnomalies=workspaceHasAnomalies===true||data.workspace_has_anomalies;
    contextsReady=true;contextsEmpty=data.projects.length===0;
    const expanded=new Set([...sessionGroups].filter(([,g])=>g.details.open).map(([path])=>path));
    const sessionsExpanded=new Set([...sessionGroups].filter(([,g])=>g.expanded).map(([path])=>path));
    for(const group of sessionGroups.values()) ++group.generation;
    projects.clear(); sessionGroups.clear();el('project-tree').replaceChildren();
    const preferredProject=selectedSession?.project || selectedProject || preferred?.project_path;
    const preferredId=selectedSession?.id || preferred?.investigation;
    const target=data.projects.find(p=>p.path===preferredProject||p.paths?.includes(preferredProject))?.path || data.projects[0]?.path;
    for(const project of data.projects) {
      for(const path of project.paths || [project.path]) projects.set(path,project);
      const details=document.createElement('details');details.className='project-group';
      const summary=block(details,'summary',projectText(project.path));summary.title=project.path;
      const makeSvg=tag=>document.createElementNS?document.createElementNS('http://www.w3.org/2000/svg',tag):document.createElement(tag);
      const icon=makeSvg('svg');icon.setAttribute('viewBox','0 0 24 24');icon.setAttribute('aria-hidden','true');icon.setAttribute('fill','none');icon.setAttribute('stroke','currentColor');icon.setAttribute('stroke-width','1.6');icon.setAttribute('stroke-linejoin','round');
      const shape=makeSvg('path');shape.setAttribute('d','M3 19V5a2 2 0 0 1 2-2h5l3 3h6a2 2 0 0 1 2 2v2M3 19l3-9h17l-3 9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z');shape.setAttribute('class','folder-open');icon.append(shape);
      const closed=makeSvg('path');closed.setAttribute('class','folder-closed');closed.setAttribute('d','M3 8V5a2 2 0 0 1 2-2h5l3 3h6a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8h18');icon.append(closed);summary.append(icon);
      bindProjectDisclosure(details,summary);
      summary.addEventListener('click',()=>{
        const group=sessionGroups.get(project.path);
        if(group?.paths.includes(selectedSession?.project))return;
        return selectProject(project.path).catch(e=>say(e.message));
      });
      const body=document.createElement('div');body.className='project-sessions';details.append(body);
      summary.tabIndex=0;
      const more=block(details,'button','展开显示');more.className='sessions-more';more.hidden=true;
      const notice=block(details,'p','');notice.className='muted';
      const group={details,summary,body,notice,more,expanded:sessionsExpanded.has(project.path),paths:project.paths||[project.path],buttons:new Map(),sessions:[],loaded:false,generation:0,pending:null};
      more.addEventListener('click',()=>{group.expanded=!group.expanded;arrangeSessions(group);more.focus({preventScroll:true});});
      sessionGroups.set(project.path,group);
      details.addEventListener('toggle',()=>{if(details.open)populateProject(project.path).catch(()=>{});});
      details.open=expanded.has(project.path)||project.path===target;el('project-tree').append(details);
    }
    const detailAtRefresh=current, detailGeneration=openGeneration;
    if(view==='detail' && detailAtRefresh?.investigation){
      const owner=[...sessionGroups].find(([,group])=>group.paths.includes(detailAtRefresh.project));
      if(owner)await populateProject(owner[0]);
      if(generation!==contextGeneration || selectionAtStart!==selectionGeneration)return;
      if(current===detailAtRefresh && openGeneration===detailGeneration &&
         (!owner || !owner[1].sessions.some(s=>s.id===detailAtRefresh.investigation)))showList();
    }
    if(!target) {
      selectedProject=null;
      if(selectionAtStart===selectionGeneration && selectedSession)clearSession();
      el('context-status').textContent='尚无已保存的项目记录。';
      if(!selectedSession) {el('list-status').textContent='';el('filters').hidden=true;}
      syncOnboarding();
      return true;
    }
    const sessions=await populateProject(target);
    if(generation!==contextGeneration || selectionAtStart!==selectionGeneration) return;
    const visible=navigationSessions(sessionGroups.get(target));
    const session=visible.find(s=>s.id===preferredId)||visible[0];
    if(!session){clearSession({preserveDetail:view==='detail'});selectedProject=target;syncProjectList();}
    if(session && selectedSession?.id!==session.id) await selectSession(target,session,{preserveView:view==='detail'});
    for(const [path,group] of sessionGroups) if(group.details.open && path!==target)populateProject(path).catch(()=>{});
    syncOnboarding();
    return true;
  } catch(error) {if(generation===contextGeneration){contextsReady=false;syncOnboarding();el('context-status').textContent='项目未能加载，请刷新项目重试。'+error.message;}return false;}
}
function clearSession({preserveDetail=false}={}) {
  selectingAnomalies=false;matchingTotal=0;resetSelection();
  ++selectionGeneration;++listGeneration;++typeGeneration;loading=false;selectedSession=null;selectedProject=null;
  if(!preserveDetail)showList();cancelSessionAction();
  el('list-view').setAttribute('aria-busy','false');
  el('project').value='';el('session').value='';el('filters').hidden=true;el('session-actions').hidden=true;
  el('records').replaceChildren();el('more').hidden=true;el('nav-return').hidden=true;el('list-empty').hidden=true;
  for(const group of sessionGroups.values())for(const nodes of group.buttons.values())nodes.button.setAttribute('aria-current','false');
  snapshotSeq=null;unfilteredTotal=null;syncRecordCount();el('session-description').textContent='从项目中选择会话';
  el('list-heading').textContent='选择会话';el('list-status').textContent='暂无可显示的会话。';
  if(!preserveDetail)history.replaceState({view:'list'},'',baseHash);
}
function cancelSessionAction() {
  ++actionGeneration;sessionAction=null;el('session-confirm').hidden=true;
}
async function prepareSessionAction(action) {
  if(!selectedSession || actionBusy)return;
  const id=selectedSession.id,name=sessionText(selectedSession),generation=++actionGeneration;
  sessionAction=null;el('session-confirm').hidden=false;el('session-confirm-apply').disabled=true;
  el('session-confirm-text').textContent='正在核对本地记录…';el('session-confirm-status').textContent='';
  try {
    const value=await (await request('/api/session-management?'+new URLSearchParams({investigation:id}))).json();
    if(generation!==actionGeneration || selectedSession?.id!==id)return;
    sessionAction={investigation:id,action,snapshot:value.snapshot};
    el('session-confirm-text').textContent=action==='delete'
      ? `永久删除“${name}”的 ${value.events} 条本地事件、${value.revisions} 个人工版本和 ${value.summaries} 份旧总结？此操作不可撤销；Codex 会话、已导出文件和备份保留。采集仍启用时，新事件可以重新出现在列表中。`
      : action==='archive'?`归档“${name}”？可在“已归档”中恢复。归档不停止采集，后续记录仍归入这个会话。`:`恢复“${name}”到当前会话列表？`;
    el('session-confirm-apply').textContent=action==='delete'?'永久删除':action==='archive'?'确认归档':'确认恢复';
    el('session-confirm-apply').disabled=false;
  } catch(error) {if(generation===actionGeneration)el('session-confirm-status').textContent='未能核对记录，请取消后重试。';}
}
async function applySessionAction() {
  if(!sessionAction || actionBusy)return;
  const action={...sessionAction};actionBusy=true;el('session-confirm-apply').disabled=true;
  try {
    const result=await (await request('/api/session-management','POST',action)).json();
    if(result.completed!==true || result.investigation!==action.investigation || result.action!==action.action)throw new Error('未取得操作确认');
    sessionViews.clear();
    if(selectedSession?.id===action.investigation || current?.investigation===action.investigation)clearSession();
    cancelSessionAction();await contexts();say(action.action==='delete'?'已删除所选会话的 Blackbox 本地记录。':action.action==='archive'?'已归档，可在“已归档”中恢复。':'已恢复到当前会话列表。');
  } catch(error) {
    sessionAction=null;el('session-confirm-status').textContent='未能确认操作结果，记录也可能已更新。请刷新记录后重新核对。';
  } finally {actionBusy=false;}
}
function renderTypes() {
  syncFilterState();
  const chosen=el('anomaly-type').value,search=el('type-search').value.trim().toLocaleLowerCase();
  el('type-label').textContent=chosen || '全部异常';el('type-options').replaceChildren();
  const options=[{name:'',count:null},...availableTypes];
  if(chosen && !availableTypes.some(t=>t.name===chosen))options.push({name:chosen,count:0});
  for(const item of options) {
    if(item.name && !item.name.toLocaleLowerCase().includes(search))continue;
    const button=block(el('type-options'),'button','');
    block(button,'span',item.name || '全部异常').className='type-name';
    if(item.name) {
      const count=block(button,'span',String(item.count));count.className='type-count';
      count.setAttribute('aria-hidden','true');
      button.setAttribute('aria-label',`${item.name}，${item.count} 条记录`);
    }
    button.type='button';button.setAttribute('aria-pressed',String(item.name===chosen));
    button.addEventListener('click',()=>{el('anomaly-type').value=item.name;el('type-picker').open=false;renderTypes();load().catch(e=>say(e.message));});
  }
  el('type-search').hidden=availableTypes.length<=8;
}
async function refreshTypes(through=snapshotSeq) {
  if(!selectedSession || through==null)return false;
  const id=selectedSession.id,generation=++typeGeneration;
  unfilteredTotal=null;syncRecordCount();
  el('type-status').textContent='正在读取类型…';
  try {
    const data=await (await request('/api/anomaly-types?'+new URLSearchParams({investigation:id,through}))).json();
    if(generation!==typeGeneration || selectedSession?.id!==id)return;
    availableTypes=data.types || [];el('type-search').value='';renderTypes();
    unfilteredTotal=availableTypes.every(t=>Number.isInteger(t.count)&&t.count>=0)?availableTypes.reduce((sum,t)=>sum+t.count,0):null;
    if(typeof data.workspace_has_anomalies==='boolean')workspaceHasAnomalies=workspaceHasAnomalies===true||data.workspace_has_anomalies;
    syncRecordCount();
    el('type-status').textContent=availableTypes.length?'':'当前会话暂无已识别的异常类型。';
  } catch(error) {if(generation===typeGeneration){el('type-status').textContent='类型与总数未能加载，请刷新记录重试。';syncRecordCount();}return false;}
  syncOnboarding();
  return true;
}
function showNavigation() {
  mobileReturnScroll=window.scrollY;el('workspace').dataset.stage='navigation';
  el('nav-return').hidden=!selectedSession&&!selectedProject;
  el('nav-return').textContent=selectedSession?'返回当前会话 →':'返回当前项目 →';
  requestAnimationFrame(()=>{(el('scope-picker').hidden?[...sessionGroups.values()][0]?.summary||el('help-open'):el('scope-label')).focus({preventScroll:true});window.scrollTo(0,0);});
}
async function start() {
  const initial=new URLSearchParams(location.hash.slice(1)),direct=initial.has('record');
  if(direct) await route().catch(()=>{});
  else history.replaceState({view:'list'},'',baseHash);
  if(initial.get('preview')==='1' && current && String(current.id)===initial.get('record') && String(current.source_seq)===initial.get('through')) await previewExport().catch(e=>say(e.message));
  let preferred=current?{project_path:current.project,investigation:current.investigation}:null;
  if(!preferred)try{preferred=await (await request('/api/reader-selection')).json();}catch(_){}
  await contexts(preferred);
}
function showList() {
  clearExportPreview();
  if(wideLayout() && view==='detail') listScroll=listPosition();
  ++openGeneration;
  view='list'; current=null;
  syncLayout(); el('export').disabled=true;
  for(const card of el('records').children) card.setAttribute('aria-current','false');
  el('help').open=listHelpOpen;el('workspace').dataset.stage='reading';
  document.title='Blackbox Plugin · 执行异常'; say(''); listScrollPending=true;
  requestAnimationFrame(()=>{
    if(view!=='list') return;
    const card=Array.from(el('records').children).find(n=>n.dataset.recordId===listFocus);
    (card || el('list-heading')).focus({preventScroll:true});
    scrollList(listScroll);
    listScrollPending=false;
  });
}
function backToList() {
  if(pendingListReturn) return;
  // Hide immediately; a late response must not reopen the detail while popstate is queued.
  showList();
  if(history.state?.returnToList) {
    let resolve;
    const promise=new Promise(done=>{resolve=done;});
    pendingListReturn={promise,resolve};
    history.back();
  }
  else history.replaceState({view:'list'},'',baseHash);
}
function route() {
  const params=new URLSearchParams(location.hash.slice(1));
  const id=params.get('record');
  if(id) return openItem(id,params.get('through'),false);
  showList();
  return Promise.resolve();
}
async function request(path, method='GET', body=undefined) {
  let response;
  try { response = window.blackboxRequest ? await window.blackboxRequest(path,method,body) : await fetch(path, {method,headers:{Authorization:`Bearer ${auth}`,...(body===undefined?{}:{'Content-Type':'application/json'})},...(body===undefined?{}:{body:JSON.stringify(body)}),signal:AbortSignal.timeout(path==='/api/cleanup'?120000:15000)}); }
  catch (error) { if(window.blackboxRequest) throw error; throw new Error('本地服务连接中断，请双击桌面或开始菜单的 Blackbox Plugin 重新打开。'); }
  if (!response.ok) throw new Error(response.status === 401 ? '本地凭据失效，请通过 Blackbox Plugin 入口重新打开。'
    : response.status === 503 ? '本地数据库暂时不可用，请稍后重试。'
    : '无法完成请求，请检查日期范围或刷新记录。');
  return response;
}
function filters() {
  const query = new URLSearchParams({project_path:el('project').value,investigation:el('project').value?el('session').value:'',anomaly_type:el('anomaly-type').value});
  const preset=el('time').value;
  if(preset==='today' || preset==='week') {
    const start=new Date(); start.setHours(0,0,0,0);
    const end=new Date(start); end.setDate(end.getDate()+1);
    if(preset==='week') start.setDate(start.getDate()-6);
    query.set('start',start.toISOString()); query.set('end',end.toISOString());
  } else if(preset==='custom') {
    for (const id of ['start','end']) {
      if (!el(id).value) continue;
      const date = new Date(el(id).value + 'T00:00:00');
      if (id === 'end') date.setDate(date.getDate()+1);
      query.set(id,date.toISOString());
    }
    if(query.has('start') && query.has('end') && query.get('start')>=query.get('end')) throw new Error('请选择有效的记录时间范围。');
  }
  return query.toString();
}
async function load(append=false) {
  if (append && (loading || nextBefore === null || nextBefore === undefined)) return;
  if(!el('project').value || !el('session').value) return;
  el('filters').hidden=false;
  const query = append ? activeFilters : filters();
  syncFilterState();
  const generation = ++listGeneration;
  if(!append) ++typeGeneration;
  const cursor = append ? '&'+new URLSearchParams({before:nextBefore,through:snapshotSeq}) : '';
  if(!append){resetSelection();matchingTotal=0;}
  loading=true;syncSelection(); el('more').disabled=true; el('list-view').setAttribute('aria-busy','true');
  el('list-loading').hidden=false;
  el('list-view').dataset.replacing=String(!append);
  say('');
  try {
    const data = await (await request('/api/anomalies?' + query + cursor)).json();
    if (generation !== listGeneration) return;
    if(data.items?.length)workspaceHasAnomalies=true;
    const typesLoaded=append?true:await refreshTypes(data.snapshot_seq);
    if(generation!==listGeneration)return;
    activeFilters = query;
    if (!append) {resetSelection();el('records').replaceChildren(); snapshotSeq=data.snapshot_seq;}
    for (const row of data.items) {
      const button = document.createElement('button'); button.className='record'; button.dataset.recordId=String(row.id);
      button.setAttribute('aria-current',String(String(current?.id) === String(row.id)));
      const top=document.createElement('span'); top.className='card-top';
      block(top,'span','!').className='error-label';
      const timestamp=block(top,'time',dateText(row.captured));timestamp.className='card-time';timestamp.dataset.captured=row.captured;
      timestamp.setAttribute('datetime',row.captured);timestamp.title=absoluteDateText(row.captured);
      if(Number.isInteger(row.repeat_count)&&row.repeat_count>0)block(top,'span',`再次调用 ${row.repeat_count} 次`).className='card-repeat';
      const title=document.createElement('strong'); title.textContent=row.title.replace(/^工具返回 /,'');
      const through=snapshotSeq;
      button.append(title);
      if(typeof row.source_summary==='string'&&row.source_summary){const summary=block(button,'span',row.source_summary);summary.className='card-summary';summary.title=typeof row.source_summary_full==='string'?row.source_summary_full:row.source_summary;}
      button.append(top); button.addEventListener('click',()=>{if(selectingAnomalies){toggleAnomaly(row.id,button);return;}return openItem(row.id,through).catch(()=>{});}); el('records').append(button);
    }
    matchingTotal=Number.isInteger(data.total_count)?data.total_count:el('records').children.length;syncSelection();
    nextBefore=data.next_before ?? null; el('more').hidden=nextBefore===null;
    el('more').textContent=data.items.length ? '加载更多' : '继续查找较早记录';
    const count=el('records').children.length;
    syncRecordCount();
    el('list-empty').hidden=count>0 || nextBefore!==null;
    el('list-status').textContent=(Number.isInteger(data.total_count) ? `${data.total_count} 条异常记录` : '')
      + (data.has_newer ? ' · 有新事件，刷新可查看' : '')
      + (!count && nextBefore!==null ? ' · 尚未扫描完，可继续查找较早记录' : '');
    return {typesLoaded};
  } catch(error) {
    if(generation===listGeneration) {
      if(!append){el('records').replaceChildren();snapshotSeq=null;nextBefore=null;el('more').hidden=true;el('list-empty').hidden=true;el('list-status').textContent='记录未能更新，请重试。';syncRecordCount();}
      throw error;
    }
  } finally {
    if (generation===listGeneration) {loading=false;syncSelection(); el('list-loading').hidden=true;el('more').disabled=false; el('list-view').setAttribute('aria-busy','false');syncOnboarding();}
  }
}
function block(parent,tag,text) { const n=document.createElement(tag); n.textContent=text; parent.append(n); return n; }
async function copyText(value) {
  try {
    if(navigator.clipboard?.writeText) {await navigator.clipboard.writeText(value);return;}
  } catch(_) { /* Embedded hosts may not grant the Clipboard API. */ }
  const active=document.activeElement, field=document.createElement('textarea');
  field.value=value;field.className='clipboard-buffer';field.setAttribute('aria-label','复制内容');
  document.body.append(field);
  try {field.select();if(!document.execCommand('copy')) throw new Error('复制未获允许');}
  finally {field.remove();active?.focus({preventScroll:true});}
}
const codeObservers=[];
function errorHighlightStart(value,type) {
  if(!/^(?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*(?:Error|Exception|Expired)$/.test(type))return -1;
  let offset=0;
  for(const line of value.split('\n')) {
    const trimmed=line.trimStart();
    if(trimmed===type || trimmed.startsWith(type+':'))return offset;
    offset+=line.length+1;
  }
  return -1;
}
function codeBlock(parent,value,errorType='') {
  const box=document.createElement('div');box.className='code-box';
  const copy=document.createElement('button');copy.className='code-copy';copy.type='button';
  const copyIcon=document.createElement('span');copyIcon.className='code-icon';copyIcon.setAttribute('aria-hidden','true');copy.append(copyIcon);
  let feedback=0;
  function copyState(state){copy.dataset.state=state;copy.setAttribute('aria-label',state==='done'?'已复制':state==='error'?'复制失败，请重试':'复制代码框内容');}
  copyState('copy');
  copy.addEventListener('click',async()=>{
    copy.disabled=true;
    const revision=++feedback;
    try {await copyText(value);copyState('done');}
    catch(_) {copyState('error');}
    finally {copy.disabled=false;setTimeout(()=>{if(revision===feedback)copyState('copy');},1600);}
  });
  box.addEventListener('mouseleave',()=>{if(!copy.disabled){++feedback;copyState('copy');}});
  const pre=document.createElement('pre');pre.className='code-content';
  const highlight=errorHighlightStart(value,errorType);
  if(highlight<0)pre.textContent=value;
  else {
    if(highlight)block(pre,'span',value.slice(0,highlight));
    block(pre,'span',value.slice(highlight)).className='error-highlight';
  }
  const toggle=document.createElement('button');toggle.type='button';toggle.className='code-expand';toggle.hidden=true;
  const arrow=document.createElement('span');arrow.className='code-chevron';arrow.setAttribute('aria-hidden','true');toggle.append(arrow);
  let expanded=false;
  function measure() {
    const long=pre.scrollHeight>280;
    toggle.hidden=!long&&!expanded;box.dataset.expandable=String(!toggle.hidden);
    box.dataset.collapsed=String(long&&!expanded);
    toggle.setAttribute('aria-label',expanded?'收起':'展开全文');toggle.setAttribute('aria-expanded',String(expanded));
  }
  toggle.addEventListener('click',()=>{expanded=!expanded;measure();if(!expanded)box.scrollIntoView?.({block:'nearest'});});
  box.append(copy,pre,toggle);parent.append(box);
  requestAnimationFrame(measure);
  if(typeof ResizeObserver!=='undefined'){const observer=new ResizeObserver(measure);observer.observe(pre);codeObservers.push(observer);}

}
function operation(content) {
  const value=content.tool_input;
  if(content.tool_name==='Bash') {
    if(typeof value?.command==='string') return value.command;
    if(typeof value?.cmd==='string') return value.cmd;
  }
  return JSON.stringify(value ?? '未捕获输入',null,2);
}
function outputText(output) {
  if(typeof output==='string') return output || '未捕获到返回正文。';
  if(output?.truncated) {
    if(typeof output.preview==='string' && typeof output.tail==='string' && Number.isInteger(output.original_length))
      return `已保留输出开头：\n${output.preview}\n\n[中间内容未保存；原长度 ${output.original_length} 字符]\n\n已保留输出末尾：\n${output.tail}`;
    return `已保存的截断预览（旧格式，可能包含 JSON 编码）：\n${output.preview || JSON.stringify(output,null,2)}`;
  }
  return output === undefined || output === null ? '未捕获到返回正文。' : JSON.stringify(output,null,2);
}
async function openItem(id, through=snapshotSeq, push=true) {
  if(push && pendingListReturn) return pendingListReturn.promise.then(()=>openItem(id,through,push));
  if(view==='list') {
    if(!listScrollPending) listScroll=listPosition();
    listFocus=String(id); listHelpOpen=el('help').open;
    // Keep the loaded list intact, including when paging/refreshing was in flight.
    ++listGeneration; loading=false; el('more').disabled=false; el('list-view').setAttribute('aria-busy','false');
  }
  if(push) {
    const hash=baseHash+'&'+new URLSearchParams({record:id,...(through!==null && through!==undefined ? {through} : {})});
    if(view==='list') {
      history.replaceState({view:'list'},'',baseHash);
      history.pushState({view:'detail',returnToList:true},'',hash);
    } else history.replaceState(history.state,'',hash);
  }
  clearExportPreview();
  detailRoute={id,through}; view='detail'; current=null; listScrollPending=false;
  say(''); el('help').open=false;
  syncLayout();
  el('detail').hidden=true; el('export').disabled=true; el('retry').hidden=true;
  el('record-heading').textContent='';
  el('raw-heading').textContent='';el('raw-heading').hidden=true;
  el('detail-status').textContent='正在读取详情…';
  if(wideLayout()) el('detail-view').scrollTop=0;
  else {el('back').focus({preventScroll:true});window.scrollTo(0,0);}
  const generation=++openGeneration;
  const selectionAtOpen=selectionGeneration;
  let data;
  try {data=await (await request('/api/anomaly/'+encodeURIComponent(id)+(through!==null && through!==undefined ? '?through='+encodeURIComponent(through) : ''))).json();}
  catch(error) {
    if(generation!==openGeneration) return;
    el('detail-status').textContent=error.message; el('retry').hidden=false;
    throw error;
  }
  if(generation!==openGeneration) return;
  current=data;
  workspaceHasAnomalies=true;
  // Browser Forward may revisit a detail from a different chat after a new selection.
  // Adopt that chat's saved list as the return destination without changing the detail cutoff.
  if(selectionAtOpen===selectionGeneration && selectedSession && data.investigation && selectedSession.id!==data.investigation) {
    const session=sessionGroups.get(data.project)?.sessions.find(s=>s.id===data.investigation)
      || {id:data.investigation,session:data.session,first_captured:data.captured};
    selectSession(data.project,session,{preserveView:true}).catch(()=>{});
  }
  el('detail').hidden=false; el('export').disabled=false; el('detail-status').textContent='';
  const rawTitle=data.title.replace(/^工具返回 /,'');
  const displayTitles={FileNotFoundError:'找不到文件',PermissionError:'没有访问权限',JSONDecodeError:'JSON 解析失败','json.decoder.JSONDecodeError':'JSON 解析失败',TimeoutExpired:'执行超时',TimeoutError:'执行超时',ModuleNotFoundError:'缺少模块',ImportError:'模块导入失败',AssertionError:'断言失败',TypeError:'类型错误',ValueError:'值错误',KeyError:'键不存在',IndexError:'索引越界',AttributeError:'属性错误',NameError:'名称未定义',SyntaxError:'语法错误',IndentationError:'缩进错误',ZeroDivisionError:'除数为零',ConnectionError:'连接错误',RuntimeError:'运行时错误'};
  el('record-heading').textContent=displayTitles[rawTitle] || rawTitle;
  el('raw-heading').textContent=rawTitle;el('raw-heading').hidden=!displayTitles[rawTitle];
  document.title=el('record-heading').textContent+' · Blackbox';
  updateHeadings();
  for (const button of el('records').children) button.setAttribute('aria-current',String(button.dataset.recordId===String(id)));
  for(const observer of codeObservers)observer.disconnect();codeObservers.length=0;
  const reading=el('reading'); reading.replaceChildren();
  if(data.capture_gap) {
    const unknown=data.capture_gap_scope==='unknown_project';
    const notice=unknown && data.capture_gap_reason==='restored_store' ? '从备份恢复后，无法确认恢复前后的记录是否连续，因此没有跨恢复时间连接后续操作。' : unknown && data.capture_gap_reason==='diagnostic_unreadable' ? '采集诊断无法读取或内容不一致，暂不连接连续性不明的操作。' : unknown ? '有一次记录未能保存，但无法确定所属项目或会话，因此没有跨过这段不确定的时间连接操作。' : '这段会话有记录未能保存，后续操作不会跨过缺失部分连接。缺失内容无法从这里恢复。';
    block(reading,'p',notice).className='notice';
  }
  if(data.legacy_association_boundary) block(reading,'p','旧记录缺少判断操作是否连续的信息，因此未附加后续调用。').className='notice';
  let repeated=null;
  for (let i=0;i<data.events.length;i++) {
    if(i===1) {repeated=block(reading,'details','');repeated.className='repeated-calls';block(repeated,'summary',`相同输入的再次调用 · ${data.events.length-1} 次`);}
    const target=i?repeated:reading;
    const event=data.events[i], content=JSON.parse(event.content);
    block(target,'h3',i ? `相同输入再次调用 ${i} · ${dateText(event.captured)}` : data.tool==='Bash' ? '执行命令' : '工具输入');
    codeBlock(target,operation(content));
    block(target,'h3',i ? '返回结果' : '错误返回');
    const output=content.tool_response;
    const renderedOutput=outputText(output);
    codeBlock(target,renderedOutput,i?'':rawTitle);
    const coverage=content._coverage?.truncated_fields || [];
    if(coverage.length) block(target,'p','部分内容过长，仅保存了片段。').className='notice';
  }
  if(repeated) block(repeated,'p','仅连接同一会话、同一回合内相同输入的调用，最多 20 次；遇到记录缺口或下一次匹配错误时停止。');
  el('record-heading').focus({preventScroll:true});
}
function clearExportPreview() {
  ++exportGeneration;exportPreview=null;
  el('export-preview').hidden=true;el('export-preview-text').textContent='';
  el('export-preview-status').textContent='';el('export-download').disabled=true;
  const hosted=typeof window.blackboxSaveExport==='function';
  el('export-save').disabled=true;el('export-save').hidden=!hosted;
  el('export-save').className='primary';el('export-download').hidden=hosted;
  el('export').disabled=!current;
}
function sameExport(snapshot) {
  return current && String(current.id)===String(snapshot.id) && current.source_seq===snapshot.through && current.rule_version===snapshot.rule;
}
async function previewExport() {
  if(!current) return;
  clearExportPreview();
  const snapshot={id:current.id,through:current.source_seq,rule:current.rule_version};
  const generation=exportGeneration;
  el('export-preview').hidden=false;el('export').disabled=true;
  el('export-preview-status').textContent='正在读取完整导出文件…';
  try {
    const response=await request('/api/anomaly-export/'+encodeURIComponent(snapshot.id)+'?'+new URLSearchParams({through:snapshot.through,rule:snapshot.rule}));
    const body=await response.text();
    if(generation!==exportGeneration || !sameExport(snapshot)) return;
    exportPreview={...snapshot,body,preview_sha256:response.headers?.get('X-Blackbox-Preview-SHA256')};el('export-preview-text').textContent=body;
    el('export-preview-status').textContent=`记录 #${snapshot.id} · 截至事件 #${snapshot.through} · 此预览已固定`;
    el('export-download').disabled=false;
    el('export-save').disabled=typeof window.blackboxSaveExport!=='function';
    el('export-preview-text').focus();el('export-preview').scrollIntoView?.({block:'nearest'});
  } catch(error) {
    if(generation!==exportGeneration || !sameExport(snapshot)) return;
    el('export-preview-status').textContent='预览未完成，请重新点击“预览导出”。';throw error;
  } finally {if(generation===exportGeneration)el('export').disabled=!current;}
}
async function download() {
  if(!exportPreview || !sameExport(exportPreview)) return;
  const snapshot=exportPreview;
  if(downloadingPreview===snapshot) return;
  const generation=exportGeneration;
  downloadingPreview=snapshot;el('export-download').disabled=true;
  el('export-preview-status').textContent='正在请求下载，请留意宿主的保存提示…';
  const visible=()=>generation===exportGeneration && exportPreview===snapshot && sameExport(snapshot);
  try {
    const name=`anomaly-${snapshot.id}-through-${snapshot.through}.md`;
    if(typeof window.blackboxDownload==='function') {
      await window.blackboxDownload(name,snapshot.body);
      if(visible()) el('export-preview-status').textContent=`宿主已接受记录 #${snapshot.id} 的下载请求，请在下载目录确认文件。`;
    } else {
      const url=URL.createObjectURL(new Blob([snapshot.body],{type:'text/markdown; charset=utf-8'}));
      const link=document.createElement('a');link.href=url;link.download=name;link.click();
      setTimeout(()=>URL.revokeObjectURL(url),1000);
      if(visible()) el('export-preview-status').textContent=`已向浏览器请求下载记录 #${snapshot.id}，请在下载目录确认文件。`;
    }
  } catch(error) {
    if(visible()) el('export-preview-status').textContent=error.message || '下载未完成，请重试。';
  } finally {
    if(downloadingPreview===snapshot) downloadingPreview=null;
    if(visible()) el('export-download').disabled=false;
  }
}
async function saveExport() {
  if(!exportPreview || !sameExport(exportPreview) || typeof window.blackboxSaveExport!=='function') return;
  const snapshot=exportPreview,generation=exportGeneration;
  if(savingPreview===snapshot) return;
  savingPreview=snapshot;el('export-save').disabled=true;
  el('export-preview-status').textContent='正在保存已预览的内容到本机下载文件夹…';
  const visible=()=>generation===exportGeneration && exportPreview===snapshot && sameExport(snapshot);
  try {
    const saved=await window.blackboxSaveExport(snapshot,visible);
    if(visible()) el('export-preview-status').textContent=saved?`已保存并校验（${saved.bytes} 字节）：${saved.path}`:'未取得保存结果，请检查下载文件夹。';
  } catch(error) {
    if(visible()) el('export-preview-status').textContent=error.message || '保存未完成，请重试。';
  } finally {
    if(savingPreview===snapshot) savingPreview=null;
    if(visible()) el('export-save').disabled=false;
  }
}
let capturePending=null,captureBusy=false,captureTrigger=null;
function captureProjectName(path) {return projects.get(path)?.name || String(path).split(/[\\/]/).filter(Boolean).pop() || path;}
function cancelCapture() {
  if(captureBusy)return;
  capturePending=null;el('capture-confirm').hidden=true;el('capture-consent').checked=false;el('privacy-drawer').inert=false;captureTrigger?.focus({preventScroll:true});
}
function confirmCapture(project) {
  if(captureBusy)return;
  captureTrigger=document.activeElement;capturePending={path:project.path,enabled:!!project.enabled};
  el('privacy-drawer').inert=true;el('capture-progress').textContent='';
  el('help-action-status').textContent='';el('capture-confirm').hidden=false;
  el('capture-confirm-title').textContent=`${project.enabled?'停用':'启用'} ${captureProjectName(project.path)} 的采集？`;
  el('capture-confirm-path').textContent=project.path;
  el('capture-confirm-text').textContent=project.enabled?'停用后，此项目不再采集新记录。已有记录仍可查看和导出。':'启用后，Blackbox 将在本机保存此项目由 Codex 送达的输入、工具调用与返回、最终回复等事件，不仅限于异常。';
  el('capture-consent').checked=false;el('capture-consent-label').hidden=project.enabled;
  el('capture-apply').textContent=project.enabled?'确认停用':'确认启用';el('capture-apply').disabled=!project.enabled;
  el('capture-cancel').focus();
}
async function applyCapture() {
  if(captureBusy || !capturePending || (!capturePending.enabled && !el('capture-consent').checked))return;
  const project=capturePending;captureBusy=true;
  el('capture-apply').disabled=true;el('capture-cancel').disabled=true;el('capture-consent').disabled=true;
  el('help-action-status').textContent='正在更新项目采集设置…';el('capture-progress').textContent='正在更新项目采集设置…';
  try {
    const result=await (await request('/api/project-management','POST',{path:project.path,enabled:!project.enabled,expected_enabled:project.enabled,accept_capture_scope:!project.enabled && el('capture-consent').checked})).json();
    if(result.project!==project.path || result.enabled!==!project.enabled)throw new Error('未取得有效确认');
    el('help-action-status').textContent=project.enabled?'已停用采集，已有记录保留。':'已启用此项目采集。';
  } catch(_) {el('help-action-status').textContent='未能确认操作结果，请刷新项目状态后重新核对。';}
  finally {
    captureBusy=false;el('capture-cancel').disabled=false;el('capture-consent').disabled=false;cancelCapture();
    await capture().catch(()=>{});
    if(el('help').open)el('help-tab-0').focus({preventScroll:true});
  }
}
el('capture-cancel').addEventListener('click',cancelCapture);
el('capture-confirm').addEventListener('click',event=>{if(event.target===el('capture-confirm'))cancelCapture();});
el('storage-help-trigger').addEventListener('click',()=>{const open=el('storage-help-text').hidden;el('storage-help-text').hidden=!open;el('storage-help-trigger').setAttribute('aria-expanded',String(open));});
el('capture-apply').addEventListener('click',applyCapture);
el('capture-consent').addEventListener('change',()=>{el('capture-apply').disabled=captureBusy || !capturePending || (!capturePending.enabled && !el('capture-consent').checked);});
el('privacy-learn').addEventListener('click',()=>selectHelpTab(1));
el('privacy-projects').addEventListener('click',()=>selectHelpTab(0));
el('capture-prompt-copy').addEventListener('click',async()=>{
  try {await navigator.clipboard.writeText(el('capture-prompt').textContent);el('capture-copy-status').textContent='已复制';}
  catch(_) {el('capture-copy-status').textContent='复制失败，请选中文字手动复制。';}
});
function captureIssue(project) {
  if(!project?.enabled)return '';
  const state=project.capture_state;
  return {hooks_untrusted:'Codex 尚未信任 Blackbox 的自动采集 Hook，当前不会收到记录。请在终端启动 Codex，输入 /hooks，审阅并信任 blackbox@personal，然后刷新。',hooks_disabled:'Blackbox 的采集 Hook 已停用。请在 Codex 的 /hooks 中检查并启用，然后刷新。',hooks_missing:'Codex 未加载完整的 Blackbox 采集 Hook，请检查插件安装并重新打开 Codex。',hooks_unknown:'暂时无法核实 Codex 的采集 Hook 信任状态，请刷新重试，或在 Codex 的 /hooks 中检查。',runtime_problem:'采集运行配置有问题，请在目标项目的 Codex 会话中检查并修复 Blackbox 运行配置。',capacity_reached:'本地存储容量已满，请在项目管理中检查并清理需要移除的记录。',review_diagnostics:'有采集诊断需要检查，最近收到记录不代表当前采集正常。'}[state]||'';
}
function syncOnboarding() {
  const ps=onboardingStatus?.projects||[], enabled=ps.filter(p=>p.enabled);
  const empty=contextsReady&&contextsEmpty&&!selectedSession&&sessionScope==='active';
  const fresh=!!onboardingStatus&&empty&&ps.length===0;
  const project=ps.find(p=>p.path===(selectedSession?.project||selectedProject))||(!selectedProject&&empty?enabled[0]:null);
  const waiting=!!onboardingStatus&&sessionScope==='active'&&!selectedSession&&!!project?.enabled&&!project.last_recorded_event&&!(project.event_count>0);
  const issue=captureIssue(project);
  const received=!!onboardingStatus&&!!selectedSession&&workspaceHasAnomalies===false&&unfilteredTotal===0&&snapshotSeq!==null&&!loading&&el('time').value==='all'&&!el('anomaly-type').value;
  const visible=view!=='detail'&&workspaceHasAnomalies!==true&&(fresh||waiting||received||!!issue);
  el('onboarding').hidden=!visible;el('reader').dataset.onboarding=String(visible);
  const navigationSlot=el(visible?'onboarding-navigation':'list-navigation');
  if(el('browse-sessions').parentNode!==navigationSlot)navigationSlot.append(el('browse-sessions'));
  el('scope-picker').hidden=visible&&!selectedSession;
  if(!visible)return;
  el('onboarding-title').textContent=fresh?'从第一个项目开始':issue?'采集状态需要检查':received?'记录已收到，暂无异常':'已启用，等待首条记录';
  el('onboarding-description').textContent=fresh?'把执行过程中出现的错误留下来。接入一个项目，在这里回看命令、错误返回与后续操作。':received?'这个会话已经有采集记录，目前没有识别到明确异常。继续工作，后续出现的异常会在这里显示。':'项目已启用本地采集。回到对应的 Codex 会话继续工作，收到记录后会显示在这里。';
  if(issue)el('onboarding-description').textContent=issue;
  if(fresh){const state=Object.values(onboardingStatus?.hook_trust||{}).find(s=>s!=='trusted');if(state)el('onboarding-description').textContent=captureIssue({enabled:true,capture_state:state});}
  for(const id of ['onboarding-start','onboarding-steps'])el(id).hidden=!fresh;
  el('onboarding-wait').hidden=fresh;el('onboarding-project').hidden=fresh;
  el('onboarding-project-name').textContent=project?captureProjectName(project.path):sessionText(selectedSession);
  el('onboarding-project-state').textContent=project?`${project.enabled?'已启用':'已停用'}${project.enabled?' · '+(captureIssue(project)?'需检查':captureReceiptText(project.last_recorded_event)):''}`:'';
  if(empty)el('context-status').textContent=fresh?'还没有接入项目。接入后，项目与会话会显示在这里。':enabled.map(p=>captureProjectName(p.path)+' · '+(captureIssue(p)?'采集状态需要检查':'等待首条记录')).join('\n');
}
function openConnect(trigger) {
  connectFocus=trigger;el('connect-prompt').textContent=el('capture-prompt').textContent;el('connect-copy-status').textContent='';
  el('connect-project').hidden=false;el('workspace').inert=true;el('help').inert=true;document.querySelector?.('.app-header')?.toggleAttribute('inert',true);el('connect-close').focus();
}
function closeConnect(){el('connect-project').hidden=true;el('help').inert=false;el('workspace').inert=el('help').open;document.querySelector?.('.app-header')?.toggleAttribute('inert',el('help').open);connectFocus?.focus();}
function captureReceiptText(event, now=new Date()) {
  return event ? `最近收到：${dateText(event.captured,now,false)}` : '等待首条记录';
}
async function capture() {
  const generation=++captureGeneration;
  let data;
  try {data=await (await request('/api/status')).json();}
  catch(error) {if(generation===captureGeneration) {onboardingStatus=null;syncOnboarding();el('capture-status').textContent='无法读取采集状态，请刷新重试。';throw error;} return;}
  if(generation!==captureGeneration) return;
  const projects=data.projects || [];
  onboardingStatus=data;syncOnboarding();
  cleanupProjects=projects;
  showCapacity(data);
  el('capture-status').textContent=`${projects.filter(p=>p.enabled).length} 个已启用 · ${projects.filter(p=>!p.enabled).length} 个已停用`;
  projectMenus.length=0;el('capture-projects').replaceChildren();
  if(!projects.length) {const empty=block(el('capture-projects'),'div','');empty.className='capture-empty';block(empty,'h3','还没有接入项目');block(empty,'p','安装插件不会自动开启采集。选择一个项目，从你确认之后开始记录。').className='muted';const button=block(empty,'button','接入第一个项目');button.className='primary';button.addEventListener('click',()=>openConnect(button));}
  for(const p of projects) {
    const card=block(el('capture-projects'),'div','');card.className='capture-project'+(p.enabled?' is-on':'');
    const name=block(card,'span',captureProjectName(p.path));name.title=p.path;name.className='capture-project-name';
    const meta=block(name,'small','');meta.className='capture-project-meta';
    const state=block(meta,'span',p.enabled?'已启用':'已停用');state.className='capture-project-state';
    if(p.enabled) {
      const receipt=block(meta,'span',(captureIssue(p)||captureReceiptText(p.last_recorded_event)));receipt.className='capture-project-receipt';
      if(p.last_recorded_event?.captured) {receipt.dataset.received=p.last_recorded_event.captured;receipt.title=absoluteDateText(p.last_recorded_event.captured);}
      const issue=captureIssue(p);if(issue){block(meta,'span','需检查').className='capture-project-issue';card.dataset.captureIssue='true';}
    }
    const folder=block(card,'span','');folder.className='capture-folder';folder.setAttribute('aria-hidden','true');
    const make=tag=>document.createElementNS?document.createElementNS('http://www.w3.org/2000/svg',tag):document.createElement(tag);
    const icon=make('svg');for(const [key,value] of Object.entries({width:'18',height:'18',viewBox:'0 0 24 24',fill:'none',stroke:'currentColor','stroke-width':'1.5','stroke-linejoin':'round'}))icon.setAttribute(key,value);
    const path=make('path');path.setAttribute('d','M3 8V5a2 2 0 0 1 2-2h5l3 3h6a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8h18');icon.append(path);folder.append(icon);
    const button=block(card,'button',p.enabled?'停用采集':'启用采集');button.disabled=captureBusy;
    button.setAttribute('aria-label',`${p.enabled?'停用':'启用'} ${captureProjectName(p.path)} 的采集`);
    button.addEventListener('click',()=>confirmCapture(p));
    const menu=block(card,'details','');menu.className='cleanup-project-menu';const trigger=block(menu,'summary','');
    const actionIcon=(d)=>{const svg=make('svg');for(const [key,value] of Object.entries({viewBox:'0 0 24 24',width:'16',height:'16',fill:'none',stroke:'currentColor','stroke-width':'1.5','stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true'}))svg.setAttribute(key,value);const path=make('path');path.setAttribute('d',d);svg.append(path);return svg;};
    const dots=actionIcon('');dots.replaceChildren();for(const x of [5,12,19]){const dot=make('circle');for(const [key,value] of Object.entries({cx:String(x),cy:'12',r:'1.6',fill:'currentColor',stroke:'none'}))dot.setAttribute(key,value);dots.append(dot);}trigger.append(dots); trigger.setAttribute('aria-label',`${captureProjectName(p.path)} 项目操作`);
    const options=block(menu,'div','');options.className='action-options';
    menu.addEventListener('toggle',()=>{if(menu.open){for(const other of projectMenus)if(other!==menu)other.open=false;fitPopup(menu,options);}});
    menu.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation?.();menu.open=false;trigger.focus();}});projectMenus.push(menu);
    for(const [action,title] of [['clear_project','清空项目记录'],['delete_project','删除项目']]) {const choice=block(options,'button','');choice.append(actionIcon(action==='clear_project'?'m16 3 5 5-12 12H5l-4-4L14 3zM7 10l7 7M9 20h12':'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7'));block(choice,'span',title);choice.className=action==='delete_project'?'danger-action':'';choice.addEventListener('click',()=>{menu.open=false;trigger.focus();return prepareCleanup(cleanupScope(action,{projects:[p.path]}));});}
  }
  const app=data.application;
  el('application-status').textContent=`插件版本：${app?.version || '未知'}`;
  const storage=data.storage, mb=value=>typeof value==='number' && Number.isFinite(value) ? (value/(1024*1024)).toFixed(1)+' MiB' : '未知';

  el('storage-value').textContent=storage ? `${mb(storage.database_bytes)} / ${mb(storage.database_limit_bytes)}` : '用量暂不可用';
  el('storage-meter').hidden=!storage || !Number.isFinite(storage.database_bytes) || !(storage.database_limit_bytes>0);
  el('storage-meter').value=storage ? Math.min(100,100*storage.database_bytes/storage.database_limit_bytes) : 0;

}
async function shutdown() {
  el('shutdown').disabled=true;
  try {await request('/api/shutdown','POST'); say('本地阅读服务已请求停止。再次双击 Blackbox Plugin 入口即可打开；项目采集开关没有改变。');}
  catch(error) {el('shutdown').disabled=false; throw error;}
}
let calendarOpen=false, calendarMonth=new Date(),calendarMode='days',pendingStart=null;
function localDateKey(date) {return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;}
function syncDates() {
  el('range-label').textContent=el('start').value&&el('end').value?`${el('start').value} — ${el('end').value}`:'选择日期范围';
}
function closeCalendar(focus=true) {
  el('calendar').hidden=true;
  el('range-open').setAttribute('aria-expanded','false');
  if(focus&&calendarOpen)el('range-open').focus();
  calendarOpen=false;pendingStart=null;
}
function chooseDate(value) {
  if(!calendarOpen)return;
  if(!value){resetFilters();el('time-label').focus();load().catch(e=>say(e.message));return;}
  if(!pendingStart){pendingStart=value;renderCalendar(value);return;}
  const range=[pendingStart,value].sort();el('start').value=range[0];el('end').value=range[1];
  syncDates();closeCalendar();load().catch(e=>say(e.message));
}
function renderCalendar(focusKey=null) {
  const year=calendarMonth.getFullYear(),month=calendarMonth.getMonth();
  el('calendar-year').textContent=calendarMode==='years'?`${year-year%12}–${year-year%12+11}`:`${year}年`;
  el('calendar-month').textContent=`${month+1}月`;
  el('calendar-week').hidden=calendarMode!=='days';
  el('calendar-hint').textContent=pendingStart?`已选 ${pendingStart}，请选择结束日期`:'选择开始日期，再选择结束日期';
  el('month-prev').setAttribute('aria-label',calendarMode==='years'?'前十二年':calendarMode==='months'?'上一年':'上个月');
  el('month-next').setAttribute('aria-label',calendarMode==='years'?'后十二年':calendarMode==='months'?'下一年':'下个月');
  const days=el('calendar-days');days.replaceChildren();days.className='calendar-days '+(calendarMode==='days'?'':'calendar-grid');
  if(calendarMode!=='days') {
    const mode=calendarMode;
    for(let i=0;i<12;i++){
      const value=mode==='years'?year-year%12+i:i,button=document.createElement('button');
      button.type='button';button.textContent=mode==='years'?`${value}年`:`${value+1}月`;
      button.setAttribute('aria-pressed',String(value===(mode==='years'?year:month)));
      button.addEventListener('click',()=>{calendarMonth=new Date(mode==='years'?value:year,mode==='months'?value:month,1);calendarMode=mode==='years'?'months':'days';renderCalendar();el('calendar-month').focus();});
      days.append(button);
    }
    fitPopup(el('date-range'),el('calendar'));return;
  }
  const offset=(new Date(year,month,1).getDay()+6)%7;
  const today=localDateKey(new Date()),selected=pendingStart||el('start').value;
  for(let i=0;i<42;i++) {
    const date=new Date(year,month,1-offset+i),key=localDateKey(date),button=document.createElement('button');
    button.type='button';button.textContent=String(date.getDate());button.dataset.date=key;
    button.className=(date.getMonth()!==month?'outside-month ':'')+(key===today?'today':'');
    if(!pendingStart&&el('start').value&&el('end').value&&key>el('start').value&&key<el('end').value)button.className+=' in-range';
    button.setAttribute('aria-label',key);button.setAttribute('aria-pressed',String(key===selected||(!pendingStart&&key===el('end').value)));
    button.addEventListener('click',()=>chooseDate(key));
    button.addEventListener('keydown',event=>{
      const steps={ArrowLeft:-1,ArrowRight:1,ArrowUp:-7,ArrowDown:7};
      if(!(event.key in steps))return;
      event.preventDefault();const next=new Date(date);next.setDate(next.getDate()+steps[event.key]);
      calendarMonth=new Date(next.getFullYear(),next.getMonth(),1);renderCalendar(localDateKey(next));
    });
    days.append(button);
  }
  fitPopup(el('date-range'),el('calendar'));
  if(focusKey)Array.from(days.children).find(b=>b.dataset.date===focusKey)?.focus();
}
function openCalendar() {
  closeCalendar(false);calendarOpen=true;
  calendarMode='days';
  const initial=el('start').value;
  const date=initial?new Date(initial+'T00:00:00'):new Date();
  calendarMonth=new Date(date.getFullYear(),date.getMonth(),1);
  el('calendar').hidden=false;el('range-open').setAttribute('aria-expanded','true');renderCalendar(localDateKey(date));
}
el('range-open').addEventListener('click',()=>calendarOpen?closeCalendar():openCalendar());
for(const [id,step] of [['month-prev',-1],['month-next',1]])el(id).addEventListener('click',()=>{if(calendarMode==='days')calendarMonth.setMonth(calendarMonth.getMonth()+step);else calendarMonth.setFullYear(calendarMonth.getFullYear()+step*(calendarMode==='years'?12:1));renderCalendar();});
el('calendar-year').addEventListener('click',()=>{calendarMode='years';renderCalendar();});
el('calendar-month').addEventListener('click',()=>{calendarMode='months';renderCalendar();});
el('date-clear').addEventListener('click',()=>chooseDate(''));
el('date-today').addEventListener('click',()=>{pendingStart=localDateKey(new Date());chooseDate(localDateKey(new Date()));});
el('date-close').addEventListener('click',()=>closeCalendar());
el('calendar').addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();closeCalendar();}});
el('calendar').addEventListener('click',event=>event.stopPropagation());
window.addEventListener('click',event=>{const path=event.composedPath?.()||[];if(calendarOpen&&!path.includes(el('calendar'))&&!el('calendar').contains(event.target)&&!el('range-open').contains(event.target))closeCalendar(false);});
function scopeChanged() {
  sessionScope=el('session-scope').value;
  el('scope-label').textContent=sessionScope==='archived'?'已归档':'当前会话';
  for(const key of ['active','archived'])el('scope-'+key).setAttribute('aria-pressed',String(key===sessionScope));
  clearSession();contexts().catch(e=>say(e.message));
}
el('session-scope').addEventListener('change',scopeChanged);
for(const key of ['active','archived'])el('scope-'+key).addEventListener('click',()=>{
  el('scope-picker').open=false;el('scope-label').focus();
  if(sessionScope===key)return;
  el('session-scope').value=key;scopeChanged();
});
el('scope-picker').addEventListener('keydown',event=>{if(event.key==='Escape'){el('scope-picker').open=false;el('scope-label').focus();}});
el('archive-session').addEventListener('click',()=>{el('session-actions').open=false;return prepareSessionAction(selectedSession?.archived?'restore':'archive');});
el('delete-session').addEventListener('click',()=>{el('session-actions').open=false;return prepareCleanup(cleanupScope('session',{investigation:el('session').value}));});
el('session-confirm-cancel').addEventListener('click',cancelSessionAction);
el('session-confirm-apply').addEventListener('click',applySessionAction);
el('type-search').addEventListener('input',renderTypes);
el('type-picker').addEventListener('keydown',event=>{if(event.key==='Escape'){el('type-picker').open=false;el('type-label').focus();}});
el('filters').addEventListener('submit',event=>{event.preventDefault();closeCalendar(false);load().catch(e=>say(e.message));});
function timeChanged() {syncTimePicker();el('date-range').hidden=el('time').value!=='custom';if(el('time').value!=='custom')load().catch(e=>say(e.message));}
el('time').addEventListener('change',timeChanged);
for(const key of Object.keys(timeOptions)) el('time-'+key).addEventListener('click',()=>{
  el('time').value=key;el('time-picker').open=false;el('time-label').focus();timeChanged();
});
el('time-picker').addEventListener('keydown',event=>{if(event.key==='Escape'){el('time-picker').open=false;el('time-label').focus();}});
const pickerIds=['time-picker','type-picker','scope-picker','session-actions'];
for(const id of pickerIds) {
  el(id).addEventListener('toggle',()=>{if(el(id).open){closeCalendar(false);for(const other of pickerIds)if(other!==id)el(other).open=false;fitPopup(el(id),el(id).querySelector?.('.time-options,.type-options,.action-options'));}});
  el(id).addEventListener('keydown',event=>{if(event.key==='Escape'){el(id).open=false;el(id==='session-actions'?'session-actions-label':id.replace('picker','label')).focus();}});
}
window.addEventListener('click',event=>{
  for(const id of pickerIds) if(!el(id).contains(event.target))el(id).open=false;
});
// Fit disclosures within their visible scroll region as well as the window.
function fitPopup(picker,panel){
  if(!panel?.getBoundingClientRect||!picker.getBoundingClientRect)return;
  const anchor=picker.getBoundingClientRect(),region=picker.closest?.('.help-content,.cleanup-dialog,#list-view')?.getBoundingClientRect();
  const top=Math.max(8,region?.top||0),bottom=Math.min(window.innerHeight-8,region?.bottom||window.innerHeight);
  const below=Math.max(0,bottom-anchor.bottom-7),above=Math.max(0,anchor.top-top-7);
  const upward=below<Math.min(panel.scrollHeight,140)&&above>below;
  picker.dataset.placement=upward?'above':'below';panel.style.maxHeight=Math.max(40,upward?above:below)+'px';
}
function fitOpenPopups(){
  if(calendarOpen)fitPopup(el('date-range'),el('calendar'));
  for(const id of pickerIds)if(el(id).open)fitPopup(el(id),el(id).querySelector?.('.time-options,.type-options,.action-options'));
  for(const menu of projectMenus)if(menu.open)fitPopup(menu,menu.querySelector?.('.action-options'));
  if(el('cleanup-scope-picker').open)fitPopup(el('cleanup-scope-picker'),el('cleanup-scope-options'));
}
window.addEventListener('resize',fitOpenPopups);
el('privacy-drawer').addEventListener('scroll',fitOpenPopups,true);
const projectMenus=[];
window.addEventListener('click',event=>{for(const menu of projectMenus)if(!menu.contains(event.target))menu.open=false;});
let helpFocus=null,helpTab=0;
function selectHelpTab(index) {
  for(const menu of projectMenus)menu.open=false;
  if(helpTab!==index){const content=document.querySelector?.('.help-content');if(content)content.scrollTop=0;}
  helpTab=index;
  for(let i=0;i<3;i++) {
    el('help-tab-'+i).setAttribute('aria-selected',String(i===index));el('help-tab-'+i).setAttribute('tabindex',i===index?'0':'-1');
    el('help-panel-'+i).hidden=i!==index;
  }
}
function syncHelp() {
  const open=el('help').open;
  el('workspace').inert=open;
  document.querySelector?.('.app-header')?.toggleAttribute('inert',open);
  for(const id of ['help-open','help-mobile'])el(id).setAttribute('aria-expanded',String(open));
  if(open){if(!el('capture-confirm').hidden)el('capture-cancel').focus();else el('help-close').focus();}
}
function openHelp(trigger) {
  helpFocus=trigger;closeCalendar(false);for(const id of pickerIds)el(id).open=false;
  if(!captureBusy)el('help-action-status').textContent='';
  el('help').open=true;syncHelp();capture().catch(()=>{});
}
function closeHelp() {
  if(captureBusy)return;
  for(const menu of projectMenus)menu.open=false;
  el('help').open=false;listHelpOpen=false;cancelCapture();syncHelp();helpFocus?.focus();
}
for(const id of ['help-open','help-mobile'])el(id).addEventListener('click',()=>openHelp(el(id)));
for(const id of ['help-close','help-done'])el(id).addEventListener('click',closeHelp);
el('help').addEventListener('toggle',syncHelp);
el('help').addEventListener('click',event=>{if(event.target===el('help'))closeHelp();});
for(let i=0;i<3;i++) {
  el('help-tab-'+i).addEventListener('click',()=>selectHelpTab(i));
  el('help-tab-'+i).addEventListener('keydown',event=>{
    const next=event.key==='ArrowRight'?(i+1)%3:event.key==='ArrowLeft'?(i+2)%3:event.key==='Home'?0:event.key==='End'?2:null;
    if(next!==null){event.preventDefault();selectHelpTab(next);el('help-tab-'+next).focus();}
  });
}
el('help').addEventListener('keydown',event=>{
  if(event.key==='Escape'){event.preventDefault();if(!el('capture-confirm').hidden)cancelCapture();else closeHelp();}
  if(event.key==='Tab') {
    const scope=el('capture-confirm').hidden?el('help'):el('capture-confirm');
    const nodes=Array.from(scope.querySelectorAll?.('button:not(:disabled),input:not(:disabled),summary,a[href],[tabindex="0"]')||[]).filter(n=>n.getClientRects().length);
    const first=nodes[0],last=nodes.at(-1);
    if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
  }
});
el('refresh').addEventListener('click',checkOnboardingStatus);
el('browse-sessions').addEventListener('click',showNavigation);
el('nav-return').addEventListener('click',()=>{el('workspace').dataset.stage='reading';requestAnimationFrame(()=>window.scrollTo(0,mobileReturnScroll));});
el('clear').addEventListener('click',()=>{resetFilters();load().catch(e=>say(e.message));});
el('more').addEventListener('click',()=>load(true).catch(e=>say(e.message)));
el('export').addEventListener('click',()=>previewExport().catch(e=>say(e.message)));
el('export-download').addEventListener('click',()=>download().catch(e=>say(e.message)));
el('export-save').addEventListener('click',()=>saveExport());
el('export-close').addEventListener('click',()=>{clearExportPreview();el('export').focus();});
el('shutdown').addEventListener('click',()=>shutdown().catch(e=>say(e.message)));
el('back').addEventListener('click',backToList);
el('retry').addEventListener('click',()=>{if(detailRoute) openItem(detailRoute.id,detailRoute.through,false).catch(()=>{});});
syncLayout();
history.scrollRestoration='manual';
window.addEventListener('popstate',()=>{
  const returning=pendingListReturn; pendingListReturn=null;
  route().catch(()=>{}).then(()=>{if(returning)returning.resolve();});
});
/* Confirmed cleanup is scoped independently of the reading-page cursor. */
function cleanupScope(action, fields={}) {return {action,projects:[],investigation:'',ids:[],anomaly_type:'',start:'',end:'',through:null,...fields};}
function resetSelection(){selectedAnomalies.clear();excludedAnomalies.clear();allMatchingSelected=false;syncSelection();}
function syncSelection(){
  const count=allMatchingSelected?Math.max(0,matchingTotal-excludedAnomalies.size):selectedAnomalies.size;
  el('selection-bar').hidden=!selectingAnomalies;el('selection-scope').hidden=!selectingAnomalies;
  el('delete-selected').hidden=!selectingAnomalies;el('delete-selected').disabled=loading||!count;
  el('selection-count').textContent=`已选 ${count} 条`;
  el('select-anomalies').textContent=selectingAnomalies?'取消':'选择';
  el('select-anomalies').disabled=loading||!el('session').value;
  el('deselect-all').hidden=!selectingAnomalies||!count;
  el('delete-filtered').textContent=allMatchingSelected&&!excludedAnomalies.size?`已选择全部 ${matchingTotal} 条`:`选择全部匹配的 ${matchingTotal} 条`;
  el('delete-filtered').setAttribute('aria-pressed',String(allMatchingSelected&&!excludedAnomalies.size));
  el('delete-filtered').disabled=loading||!matchingTotal;
  el('records').dataset.selecting=String(selectingAnomalies);
  for(const card of el('records').children){card.dataset.selected=String(allMatchingSelected?!excludedAnomalies.has(Number(card.dataset.recordId)):selectedAnomalies.has(Number(card.dataset.recordId)));if(selectingAnomalies)card.setAttribute('aria-pressed',card.dataset.selected);else card.removeAttribute?.('aria-pressed');}
}
function toggleAnomaly(id){
  if(loading)return;
  id=Number(id);const selection=allMatchingSelected?excludedAnomalies:selectedAnomalies;
  selection.has(id)?selection.delete(id):selection.add(id);syncSelection();
}
function selectedCleanupScope(){
  if(!allMatchingSelected)return cleanupScope('anomalies',{investigation:el('session').value,ids:[...selectedAnomalies]});
  const f=new URLSearchParams(activeFilters);
  return cleanupScope('anomalies',{investigation:el('session').value,anomaly_type:f.get('anomaly_type')||'',start:f.get('start')||'',end:f.get('end')||'',through:snapshotSeq,...(excludedAnomalies.size?{excluded_ids:[...excludedAnomalies]}:{})});
}
function showCleanup(){
  closeCleanupCalendar();el('cleanup-scope-picker').open=false;
  cleanupTrigger=document.activeElement;el('cleanup-dialog').hidden=false;
  el('workspace').inert=true;el('help').inert=true;document.querySelector?.('.app-header')?.toggleAttribute('inert',true);el('cleanup-result').textContent='';
  el('cleanup-summary').replaceChildren();el('cleanup-danger').hidden=true;
  el('cleanup-confirm-label').hidden=true;el('cleanup-confirm-word').value='';
  el('cleanup-apply').disabled=true;el('cleanup-retry').hidden=true;
  el('cleanup-back').hidden=true;el('cleanup-date-fields').hidden=true;el('cleanup-step').hidden=true;el('cleanup-preview').hidden=true;el('cleanup-apply').hidden=false;cleanupPlan=null;cleanupToken=null;
  el('cleanup-cancel').textContent='取消';el('cleanup-cancel').focus();
}
function closeCleanup(){if(cleanupBusy)return;closeCleanupCalendar();el('cleanup-scope-picker').open=false;cleanupPlan=null;el('cleanup-apply').disabled=true;++cleanupGeneration;el('cleanup-dialog').hidden=true;el('workspace').inert=el('help').open;el('help').inert=false;document.querySelector?.('.app-header')?.toggleAttribute('inert',el('help').open);cleanupTrigger?.focus();}
async function cleanupRequest(operation,value){return (await request('/api/cleanup','POST',{operation,value})).json();}
const cleanupTitles={anomalies:'删除异常',session:'删除会话记录',clear_project:'清空项目记录',delete_project:'删除项目并停用采集',dates:'清理历史记录'};
async function prepareCleanup(scope, alreadyOpen=false){
  if(cleanupBusy)return;if(!alreadyOpen)showCleanup();
  const generation=++cleanupGeneration;cleanupPlan=null;el('cleanup-apply').disabled=true;el('cleanup-summary').replaceChildren();el('cleanup-danger').hidden=true;el('cleanup-confirm-label').hidden=true;
  closeCleanupCalendar();el('cleanup-scope-picker').open=false;el('cleanup-confirm-word').value='';el('cleanup-title').textContent=cleanupTitles[scope.action];el('cleanup-result').textContent='正在计算实际删除范围…';
  try {
    const p=await cleanupRequest('preview',scope);if(generation!==cleanupGeneration)return;
    cleanupPlan=p;const box=el('cleanup-summary');
    block(box,'p',p.scope.investigation?`会话：${sessionText({id:p.scope.investigation})}`:'项目范围：');
    for(const path of p.projects)block(box,'p',`${captureProjectName(path)} · ${path}`).className='cleanup-path';
    if(p.scope.action==='dates')block(box,'p',`保存日期：${p.scope.start?localDateKey(new Date(p.scope.start)):'最早记录'} 起，截至 ${localDateKey(new Date(new Date(p.scope.end).getTime()-1))}（含当天）。`);
    else if(p.scope.start||p.scope.end)block(box,'p',`保存时间：${p.scope.start?absoluteDateText(p.scope.start):'最早记录'} 起，至 ${p.scope.end?absoluteDateText(p.scope.end)+'（不含）':'预览截止'}。`);
    if(p.scope.action==='anomalies')block(box,'p',`异常类型：${p.scope.anomaly_type||'全部类型'}。${p.scope.ids.length?'仅选中的异常':'包含尚未加载的筛选结果'+(p.scope.excluded_ids?.length?`，已排除 ${p.scope.excluded_ids.length} 条`:'')}；另清理 ${p.exclusive_related} 条专属关联事件，共享来源保留。以后新发生的同类异常仍会记录。`);
    block(box,'p',`涉及 ${p.sessions} 个会话；删除 ${p.events} 条事件，其中异常 ${p.anomalies} 条、其他事件 ${p.other_events} 条。范围包含 ${p.archived_sessions} 个已归档会话。`);
    if(p.versions||p.retained_versions)block(box,'p',`删除历史版本 ${p.versions} 个，保留 ${p.retained_versions} 个。${p.retained_versions?'保留的历史版本可能仍含已清理来源的内容。':''}`);
    block(box,'p',`待删除内容 ${(p.content_bytes/1048576).toFixed(2)} MiB；实际空间释放量在完成后显示。`);
    block(box,'p',p.scope.action==='delete_project'?'删除所列路径的管理项并停止其采集。':p.scope.action==='session'?'项目采集设置不变。宿主会话的新事件可能重新形成会话记录。':'项目采集设置不变，预览后新到达的记录保留。');
    el('cleanup-danger').hidden=!p.can_apply;
    el('cleanup-confirm-label').hidden=!(['clear_project','delete_project'].includes(p.scope.action)||(p.scope.action==='dates'&&!scope.projects.length&&!scope.investigation));
    el('cleanup-confirm-caption').textContent=`输入“${cleanupConfirmWord()}”确认`;el('cleanup-confirm-word').placeholder=cleanupConfirmWord();
    el('cleanup-apply').hidden=false;el('cleanup-preview').hidden=true;el('cleanup-step').textContent='选择范围 → 预览并确认';
    if(p.scope.action==='dates'){el('cleanup-date-fields').hidden=true;el('cleanup-back').hidden=false;}
    el('cleanup-apply').textContent=cleanupTitles[p.scope.action];syncCleanupConfirm();
    el('cleanup-result').textContent=p.can_apply?'':'没有匹配记录，无需删除。';
  }catch(e){if(generation===cleanupGeneration)el('cleanup-result').textContent=e.message+' 删除未执行，请重新预览。';}
}
function cleanupConfirmWord(){return cleanupPlan?.scope.action==='clear_project'?'清空':cleanupPlan?.scope.action==='dates'?'清理':'删除';}
function syncCleanupConfirm(){el('cleanup-apply').disabled=cleanupBusy||!cleanupPlan?.can_apply||(!el('cleanup-confirm-label').hidden&&el('cleanup-confirm-word').value!==cleanupConfirmWord());}
async function refreshAfterCleanup(){
  sessionViews.clear();resetSelection();clearSession();
  const failures=[];
  if(!await contexts())failures.push('项目列表');
  try{await capture();}catch{failures.push('采集状态');}
  return failures;
}
function cleanupResult(result){
  el('cleanup-back').hidden=true;cleanupToken=result.token;el('cleanup-retry').hidden=result.reclaimed;
  el('cleanup-result').textContent=result.reclaimed?`已删除 ${result.events} 条事件，空间回收完成。释放可用容量 ${result.freed_capacity_bytes>0&&result.freed_capacity_bytes<10485?'少于 0.01':(result.freed_capacity_bytes/1048576).toFixed(2)} MiB，当前使用 ${(result.database_bytes/1048576).toFixed(2)} MiB。受限期间的事件不会自动补回。`:'记录已删除，但空间回收尚未完成。请重试空间回收；不会再次删除记录。';
  el('cleanup-cancel').textContent='关闭';
}
async function applyCleanup(retry=false){
  if(cleanupBusy||(!retry&&!cleanupPlan))return;
  let completed=false;
  cleanupBusy=true;el('cleanup-back').disabled=true;el('cleanup-date-fields').inert=true;el('cleanup-close').disabled=true;syncCleanupConfirm();el('cleanup-cancel').disabled=true;el('cleanup-retry').disabled=true;el('cleanup-preview').disabled=true;
  el('cleanup-result').textContent='正在删除并回收空间，请稍候…';
  try {
    const p=cleanupPlan;if(!retry)cleanupToken=p.token;
    const result=await cleanupRequest(retry?'retry':'apply',retry?{token:cleanupToken}:{scope:p.scope,token:p.token});
    cleanupResult(result);cleanupPlan=null;completed=result.reclaimed===true;
    const failures=await refreshAfterCleanup();
    if(failures?.length)el('cleanup-result').textContent+=' 页面刷新未完成：'+failures.join('、')+'。请刷新记录；已确认的删除结果不受影响。';
  }catch(e){cleanupPlan=null;el('cleanup-result').textContent=e.message+' 未取得完成确认。请先重试空间回收核对已提交结果；若没有已提交结果，请关闭后重新预览。';el('cleanup-retry').hidden=false;}
  finally{cleanupBusy=false;el('cleanup-back').disabled=false;el('cleanup-date-fields').inert=false;el('cleanup-close').disabled=false;el('cleanup-cancel').disabled=false;el('cleanup-retry').disabled=false;el('cleanup-preview').disabled=false;syncCleanupConfirm();if(completed){const message=el('cleanup-result').textContent;closeCleanup();say(message);}}
}
function showCapacity(data){
  const storage=data.storage;const notices=[];
  if(storage?.capacity_reached)notices.push('本地存储已满，新事件无法保存。请在“采集与隐私 → 项目管理”中清理记录。');
  else if(storage&&storage.database_bytes>=storage.database_limit_bytes*.8)notices.push('本地存储已使用超过 80%，可在项目管理中清理不再需要的记录。');
  if(storage?.limited_sessions?.length)notices.push('会话额度已满：'+storage.limited_sessions.map(s=>captureProjectName(s.project)+' / '+sessionText({id:s.id})).join('、')+'。可清理对应会话的记录；其他未满会话不受此限制。');
  el('capacity-warning').hidden=!notices.length;el('capacity-warning').textContent=notices.join(' ');
  el('storage-warning').hidden=!notices.length;el('storage-warning').textContent=notices.join(' ');
  el('cleanup-pending').replaceChildren();
  for(const r of storage?.cleanup_pending||[]){const button=block(el('cleanup-pending'),'button','有已删除记录等待空间回收 · 重试');button.addEventListener('click',()=>{showCleanup();cleanupToken=r.token;return applyCleanup(true);});}
}
async function openDateCleanup(){
  showCleanup();el('cleanup-title').textContent='清理历史记录';el('cleanup-date-fields').hidden=false;
  el('cleanup-step').hidden=false;el('cleanup-step').textContent='选择范围 → 预览并确认';el('cleanup-preview').hidden=false;el('cleanup-apply').hidden=true;
  const options=el('cleanup-scope-options');options.replaceChildren();
  const choices=[...(selectedSession?[['session','当前会话']]:[]),...cleanupProjects.map(p=>[p.path,captureProjectName(p.path)+' 项目']),['all','全部本地项目']];
  const pick=(value,label)=>{el('cleanup-scope').value=value;el('cleanup-scope-label').textContent=label;for(const b of options.children)b.setAttribute('aria-pressed',String(b.dataset.value===value));el('cleanup-scope-picker').open=false;};
  for(const [value,label] of choices){const button=block(options,'button',label);button.type='button';button.dataset.value=value;button.title=value==='all'||value==='session'?label:value;button.addEventListener('click',()=>{if(cleanupBusy)return;pick(value,label);invalidateCleanup();});}
  pick(...choices[0]);el('cleanup-start').value='';el('cleanup-end').value='';syncCleanupDates();
}
function invalidateCleanup(){
  if(cleanupBusy)return;
  cleanupPlan=null;++cleanupGeneration;el('cleanup-confirm-word').value='';el('cleanup-summary').replaceChildren();el('cleanup-danger').hidden=true;el('cleanup-confirm-label').hidden=true;el('cleanup-apply').disabled=true;
  if(!el('cleanup-date-fields').hidden){el('cleanup-preview').hidden=false;el('cleanup-apply').hidden=true;}
  el('cleanup-result').textContent='范围已改变，请重新预览。';
}
el('select-anomalies').addEventListener('click',()=>{selectingAnomalies=!selectingAnomalies;resetSelection();});
el('deselect-all').addEventListener('click',resetSelection);
el('delete-selected').addEventListener('click',()=>{if(!loading&&(allMatchingSelected?Math.max(0,matchingTotal-excludedAnomalies.size):selectedAnomalies.size))return prepareCleanup(selectedCleanupScope());});
el('delete-filtered').addEventListener('click',()=>{if(loading||!selectingAnomalies||!matchingTotal)return;selectedAnomalies.clear();allMatchingSelected=!allMatchingSelected||excludedAnomalies.size>0;excludedAnomalies.clear();syncSelection();});
el('delete-anomaly').addEventListener('click',()=>{if(current)return prepareCleanup(cleanupScope('anomalies',{investigation:current.investigation,ids:[Number(current.id)]}));});
el('cleanup-open').addEventListener('click',openDateCleanup);
el('cleanup-preview').addEventListener('click',()=>{
  const scope=el('cleanup-scope').value,start=el('cleanup-start').value,end=el('cleanup-end').value;
  if(cleanupBusy)return;
  if(!end){el('cleanup-result').textContent='请选择结束日期。';return;}
  if(start&&start>end){el('cleanup-result').textContent='开始日期不能晚于结束日期。';return;}
  const boundary=new Date(end+'T00:00:00');boundary.setDate(boundary.getDate()+1);
  return prepareCleanup(cleanupScope('dates',{projects:scope!=='all'&&scope!=='session'?[scope]:[],investigation:scope==='session'?selectedSession?.id||'':'',start:start?new Date(start+'T00:00:00').toISOString():'',end:boundary.toISOString()}),true);
});
for(const id of ['cleanup-start','cleanup-end','cleanup-scope'])el(id).addEventListener('change',invalidateCleanup);
el('cleanup-confirm-word').addEventListener('input',syncCleanupConfirm);
el('cleanup-back').addEventListener('click',()=>{if(cleanupBusy)return;el('cleanup-date-fields').hidden=false;el('cleanup-back').hidden=true;invalidateCleanup();el('cleanup-scope-label').focus();});
el('cleanup-cancel').addEventListener('click',closeCleanup);el('cleanup-close').addEventListener('click',closeCleanup);
el('cleanup-dialog').addEventListener('click',event=>{if(event.target===el('cleanup-dialog')&&el('cleanup-calendar').hidden&&!el('cleanup-scope-picker').open)closeCleanup();});
el('cleanup-apply').addEventListener('click',()=>{if(!el('cleanup-apply').disabled)return applyCleanup();});
el('cleanup-retry').addEventListener('click',()=>applyCleanup(true));
el('cleanup-dialog').addEventListener('keydown',event=>{
  if(event.key==='Escape'){event.preventDefault();if(!el('cleanup-calendar').hidden)closeCleanupCalendar(true);else if(el('cleanup-scope-picker').open){el('cleanup-scope-picker').open=false;el('cleanup-scope-label').focus();}else closeCleanup();}
  if(event.key==='Tab'){const nodes=[...el('cleanup-dialog').querySelectorAll('button,input,select')].filter(n=>!n.disabled&&n.getClientRects().length);const first=nodes[0],last=nodes.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}}
});

// Independent calendar state: cleanup dates never alter the list filter calendar.
let cleanupDateSide='start',cleanupDateMode='days',cleanupDateYear=0,cleanupDateMonth=0;
function syncCleanupDates(){for(const side of ['start','end'])el('cleanup-'+side+'-open').textContent=el('cleanup-'+side).value?.replaceAll('-',' / ')||(side==='start'?'不限开始日期':'选择结束日期');}
function closeCleanupCalendar(focus=false){el('cleanup-calendar').hidden=true;for(const side of ['start','end'])el('cleanup-'+side+'-open').setAttribute('aria-expanded','false');if(focus)el('cleanup-'+cleanupDateSide+'-open').focus();}
function pickCleanupDate(value){if(cleanupBusy)return;el('cleanup-'+cleanupDateSide).value=value;syncCleanupDates();invalidateCleanup();closeCleanupCalendar(true);}
function renderCleanupCalendar(){
  el('cleanup-calendar-year').textContent=cleanupDateMode==='years'?`${cleanupDateYear-5} — ${cleanupDateYear+6}`:`${cleanupDateYear}年`;
  el('cleanup-calendar-month').textContent=`${cleanupDateMonth+1}月`;
  el('cleanup-calendar-week').hidden=cleanupDateMode!=='days';
  const grid=el('cleanup-calendar-days');grid.replaceChildren();grid.className='calendar-days'+(cleanupDateMode==='days'?'':' cleanup-calendar-picks');
  const button=(text,pressed,action)=>{const b=block(grid,'button',String(text));b.type='button';b.setAttribute('aria-pressed',String(pressed));b.addEventListener('click',action);};
  if(cleanupDateMode==='years'){for(let year=cleanupDateYear-5;year<=cleanupDateYear+6;year++)button(year,year===cleanupDateYear,()=>{cleanupDateYear=year;cleanupDateMode='months';renderCleanupCalendar();});return;}
  if(cleanupDateMode==='months'){for(let month=0;month<12;month++)button(`${month+1}月`,month===cleanupDateMonth,()=>{cleanupDateMonth=month;cleanupDateMode='days';renderCleanupCalendar();});return;}
  const offset=(new Date(cleanupDateYear,cleanupDateMonth,1).getDay()+6)%7,count=new Date(cleanupDateYear,cleanupDateMonth+1,0).getDate();
  for(let n=0;n<offset;n++)block(grid,'span','');
  for(let day=1;day<=count;day++){const value=`${cleanupDateYear}-${String(cleanupDateMonth+1).padStart(2,'0')}-${String(day).padStart(2,'0')}`;button(day,el('cleanup-'+cleanupDateSide).value===value,()=>pickCleanupDate(value));}
}
for(const side of ['start','end'])el('cleanup-'+side+'-open').addEventListener('click',()=>{
  if(cleanupBusy)return;
  if(!el('cleanup-calendar').hidden&&cleanupDateSide===side){closeCleanupCalendar();return;}
  cleanupDateSide=side;cleanupDateMode='days';const date=el('cleanup-'+side).value?new Date(el('cleanup-'+side).value+'T00:00:00'):new Date();
  cleanupDateYear=date.getFullYear();cleanupDateMonth=date.getMonth();el('cleanup-scope-picker').open=false;el('cleanup-calendar').hidden=false;
  for(const key of ['start','end'])el('cleanup-'+key+'-open').setAttribute('aria-expanded',String(side===key));renderCleanupCalendar();
});
el('cleanup-calendar-year').addEventListener('click',()=>{cleanupDateMode=cleanupDateMode==='years'?'days':'years';renderCleanupCalendar();});
el('cleanup-calendar-month').addEventListener('click',()=>{cleanupDateMode=cleanupDateMode==='months'?'days':'months';renderCleanupCalendar();});
function shiftCleanupMonth(delta){if(cleanupDateMode==='years')cleanupDateYear+=delta*12;else if(cleanupDateMode==='months')cleanupDateYear+=delta;else{const date=new Date(cleanupDateYear,cleanupDateMonth+delta,1);cleanupDateYear=date.getFullYear();cleanupDateMonth=date.getMonth();}renderCleanupCalendar();}
el('cleanup-month-prev').addEventListener('click',()=>shiftCleanupMonth(-1));el('cleanup-month-next').addEventListener('click',()=>shiftCleanupMonth(1));
el('cleanup-date-clear').addEventListener('click',()=>pickCleanupDate(''));el('cleanup-date-close').addEventListener('click',()=>closeCleanupCalendar(true));
el('cleanup-date-today').addEventListener('click',()=>{const d=new Date();pickCleanupDate(`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`);});
el('cleanup-scope-picker').addEventListener('toggle',()=>{if(el('cleanup-scope-picker').open){closeCleanupCalendar();fitPopup(el('cleanup-scope-picker'),el('cleanup-scope-options'));}});
window.addEventListener('click',event=>{
  const path=event.composedPath?.()||[];
  if(!el('cleanup-scope-picker').contains(event.target))el('cleanup-scope-picker').open=false;
  if(!path.includes(el('cleanup-calendar'))&&!el('cleanup-calendar').contains(event.target)&&!['start','end'].some(side=>el('cleanup-'+side+'-open').contains(event.target)))closeCleanupCalendar();
});
el('onboarding-connect').addEventListener('click',()=>openConnect(el('onboarding-connect')));
el('onboarding-privacy').addEventListener('click',()=>{openHelp(el('onboarding-privacy'));selectHelpTab(1);});
el('onboarding-status').addEventListener('click',()=>{openHelp(el('onboarding-status'));selectHelpTab(0);});
for(const id of ['connect-close','connect-done'])el(id).addEventListener('click',closeConnect);
el('connect-privacy').addEventListener('click',()=>{closeConnect();openHelp(connectFocus);selectHelpTab(1);});
el('connect-copy').addEventListener('click',async()=>{try{await navigator.clipboard.writeText(el('connect-prompt').textContent);el('connect-copy-status').textContent='已复制，请回到目标项目的 Codex 会话中发送。';}catch{el('connect-copy-status').textContent='请选中上方提示词，手动复制。';}});
el('connect-project').addEventListener('click',e=>{if(e.target===el('connect-project'))closeConnect();});
el('connect-project').addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();closeConnect();}if(e.key==='Tab'){const nodes=[...el('connect-project').querySelectorAll('button')].filter(n=>!n.disabled&&n.getClientRects().length);if(e.shiftKey&&document.activeElement===nodes[0]){e.preventDefault();nodes.at(-1)?.focus();}else if(!e.shiftKey&&document.activeElement===nodes.at(-1)){e.preventDefault();nodes[0]?.focus();}}});
let onboardingRefreshing=false;
async function checkOnboardingStatus() {
  if(onboardingRefreshing)return;
  onboardingRefreshing=true;
  const ids=['refresh','onboarding-refresh','onboarding-check','connect-check','onboarding-retry'];
  for(const id of ids)el(id).disabled=true;
  el('onboarding-feedback').textContent='正在检查…';say('正在检查接入状态…');el('onboarding-retry').hidden=true;
  const failures=[];
  try {
    if(await contexts()!==true)failures.push('项目列表');
    if(selectedSession)try{const result=await load();if(result?.typesLoaded===false)failures.push('类型与总数');}catch{failures.push('异常记录');}
    try{await capture();}catch{failures.push('采集状态');}
    const message=failures.length?`部分更新失败：${failures.join('、')}。请重试。`:'已更新项目与采集状态。';
    el('onboarding-feedback').textContent=message;say(message);el('onboarding-retry').hidden=!failures.length;
  } finally {onboardingRefreshing=false;for(const id of ids)el(id).disabled=false;}
}
for(const id of ['onboarding-refresh','onboarding-check','onboarding-retry'])el(id).addEventListener('click',checkOnboardingStatus);
el('connect-check').addEventListener('click',()=>{closeConnect();return checkOnboardingStatus();});
syncSelection();

if(location.protocol==='file:') {
  el('time-picker').hidden=true;
  el('file-preview').hidden=false; el('capture-status').textContent='文件预览模式，未连接记录服务。';
  el('type-picker').hidden=true;
  for(const id of ['apply','refresh','clear','more','export','browse-sessions','time','anomaly-type','start','end','shutdown']) el(id).disabled=true;
} else {
  start().catch(e=>say(e.message));capture().catch(e=>{el('capture-status').textContent=e.message;el('capture-status').textContent='无法读取采集状态，请刷新重试。';});
}
