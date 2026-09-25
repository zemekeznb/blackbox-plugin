'use strict';
const element = id => document.getElementById(id);
const token = new URLSearchParams(location.hash.slice(1)).get('token');
let selected = null;
let dirty = false;
let busy = false;
let openSequence = 0;
let editSequence = 0;
let listSequence = 0;
let captureSequence = 0;
function status(message) { element('status').textContent = message; }
function dateLabel(stamp) {
  const date = new Date(stamp);
  return Number.isNaN(date.getTime()) ? '时间未知' : date.toLocaleString();
}
function recordSummary(record) {
  const count = kind => record.events.filter(event => event.kind === kind).length;
  element('record-summary').textContent = `已捕获 ${count('UserPromptSubmit')} 次输入、${count('PostToolUse')} 次工具操作、${count('Stop')} 次 Agent 最后陈述。${record.revision ? `人工版本 ${record.revision.number}，来源截至事件 ${record.revision.source_seq}。` : '尚未保存人工版本。'}这些数字不代表过程完整或问题已解决。`;
}
async function captureStatus() {
  const sequence = ++captureSequence;
  try {
    const value = await (await api('/api/status')).json();
    if (sequence !== captureSequence) return;
    const runtime = {missing: 'Python 尚未配置', invalid: 'Python 配置无法识别，请重新配置',
      target_missing: '配置中的 Python 已不存在', configured: 'Python 路径已配置（未做执行检查）'};
    element('capture-status').textContent = `${runtime[value.runtime.state] || 'Python 配置未知'} · ${value.record_count} 份本地记录`;
    const projects = element('capture-projects'); projects.replaceChildren();
    if (!value.projects.length) {
      const empty = document.createElement('p'); empty.textContent = '尚未选择记录项目。安装插件不会自动开启采集。'; projects.append(empty);
    }
    for (const project of value.projects) {
      const row = document.createElement('div'); row.className = 'capture-project';
      const title = document.createElement('strong'); title.textContent = `${project.enabled ? '已启用' : '已停用'} · ${project.path}`;
      const last = document.createElement('p'); last.className = 'muted';
      last.textContent = project.last_recorded_event
        ? `最近落盘：${dateLabel(project.last_recorded_event.captured)} · ${project.record_count} 份记录 / ${project.event_count} 个事件。${project.enabled ? '有历史记录不代表当前持续正常。' : '停用后保留已有记录。'}`
        : '尚未收到记录。无法区分还没有活动，还是 Hook 没有投递。';
      row.append(title, last); projects.append(row);
    }
    const failure = value.last_capture_error;
    element('capture-error').hidden = !failure;
    element('capture-error').textContent = failure
      ? `最近失败标记：${failure.code} · ${failure.time ? dateLabel(failure.time) : '时间未知'}。这是全局历史标记，未定位到具体项目，也不表示当前仍失败。` : '';
  } catch (error) {
    if (sequence !== captureSequence) return;
    element('capture-status').textContent = `状态读取失败：${error.message}`;
    element('capture-projects').replaceChildren(); element('capture-error').hidden = true;
  }
}
async function api(path, body) {
  const response = await fetch(path, {
    method: body ? 'POST' : 'GET',
    headers: {Authorization: `Bearer ${token || ''}`, ...(body ? {'Content-Type': 'application/json'} : {})},
    ...(body ? {body: JSON.stringify(body)} : {})
  });
  if (!response.ok) {
    if (response.status === 409) throw new Error('已有新事件或其他人工版本。当前编辑仍在页面内，请先复制保存，再重新打开记录核对。');
    if (response.status === 401) throw new Error('缺少本地审阅凭据，请使用启动程序显示的完整链接打开。');
    throw new Error('操作未完成，请检查输入或本地程序。');
  }
  return response;
}
async function list() {
  const sequence = ++listSequence;
  const rows = await (await api(`/api/records?q=${encodeURIComponent(element('query').value)}`)).json();
  if (sequence !== listSequence) return;
  const container = element('records'); container.replaceChildren();
  for (const row of rows) {
    const button = document.createElement('button'); button.className = 'record';
    const title = document.createElement('strong'); title.textContent = row.display_title || `调查 ${row.id.slice(0,8)}`;
    const meta = document.createElement('small'); meta.textContent = `${row.event_count} 个事件 · ${row.title_source === 'human' ? '人工标题' : '首条输入 / 默认名称'} · ${row.project}`;
    button.append(title, meta); button.addEventListener('click', () => openRecord(row.id).catch(e => status(e.message)));
    container.append(button);
  }
  status(rows.length ? `找到 ${rows.length} 份本地记录。` : '暂无记录。请先为项目启用记录；安装插件本身不会授权采集。');
}
async function openRecord(id) {
  if (busy) return;
  if (dirty && !confirm('当前编辑尚未保存。离开会丢失页面内的编辑，是否继续？')) return;
  const sequence = ++openSequence;
  const edits = editSequence;
  const record = await (await api(`/api/record/${encodeURIComponent(id)}`)).json();
  if (sequence !== openSequence) return;
  if (edits !== editSequence) {status('打开期间有新增编辑，已保留当前内容。请保存后再切换记录。'); return;}
  selected = record; dirty = false;
  element('empty').hidden = true; element('detail').hidden = false;
  element('record-heading').textContent = record.display_title;
  element('meta').textContent = `${record.project} · ${record.events.length} 个事件 · 仅本地`;
  element('title').value = record.display_title;
  recordSummary(record);
  element('body').value = record.revision?.body || (record.draft.length <= 200000 ? record.draft : '来源材料超过人工版本容量。请参考下方草稿与来源，整理为不超过 200000 字符的记录。');
  element('draft').textContent = record.draft;
  element('export').disabled = !record.revision;
  const events = element('events'); events.replaceChildren();
  for (const event of record.events) {
    const detail = document.createElement('details');
    const summary = document.createElement('summary');
    const labels = {SessionStart: '会话开始', UserPromptSubmit: '用户输入', PostToolUse: '工具操作',
      Stop: 'Agent 最后陈述', PreCompact: '压缩前', PostCompact: '压缩后', Interrupt: '用户中断', SessionEnd: '会话结束'};
    summary.textContent = `#${event.seq} ${labels[event.kind] || event.kind} · ${dateLabel(event.captured)}`;
    const pre = document.createElement('pre');
    pre.textContent = `#${event.seq} ${event.kind} · ${event.captured}\nSHA-256 ${event.digest}\n${JSON.stringify(JSON.parse(event.content), null, 2)}`;
    detail.append(summary, pre); events.append(detail);
  }
  status(record.revision && record.revision.source_seq < record.source_seq
    ? '有新增材料。已保留上次人工版本，请展开自动草稿对照后再保存。'
    : '来源中的内容仅供审阅；页面不会执行其中的命令或 HTML。');
}
async function save() {
  if (!selected || busy) return;
  const snapshot = {id: selected.id,
    title: element('title').value, body: element('body').value,
    source_seq: selected.source_seq, expected_revision: selected.revision?.number || 0};
  busy = true; ++openSequence; element('save').disabled = true;
  try {
    const result = await (await api('/api/revisions', snapshot)).json();
    selected.revision = {...snapshot, ...result};
    recordSummary(selected);
    element('record-heading').textContent = snapshot.title;
    dirty = element('title').value !== snapshot.title || element('body').value !== snapshot.body;
    element('export').disabled = false;
    status(`人工版本 ${result.number} 已保存。${dirty ? '保存期间的新增编辑仍未保存。' : '尚未发布。'}`);
  } finally {busy = false; element('save').disabled = false;}
}
async function exportSaved() {
  if (!selected?.revision) return;
  const response = await api(`/api/export/${encodeURIComponent(selected.revision.id)}`);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a'); link.href = url;
  link.download = `investigation-${selected.id}-v${selected.revision.number}.md`;
  link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  status('已请求下载上次保存的人工版本；没有上传或 Git 推送。');
}
for (const id of ['title', 'body']) element(id).addEventListener('input', () => {dirty = true; ++editSequence;});
for (const [id, action] of [['search', list], ['refresh', list], ['save', save], ['export', exportSaved]]) {
  element(id).addEventListener('click', () => action().catch(e => status(e.message)));
}
element('refresh-capture').addEventListener('click', captureStatus);
element('query').addEventListener('keydown', event => {if (event.key === 'Enter') list().catch(e => status(e.message));});
window.addEventListener('beforeunload', event => {if (dirty) {event.preventDefault(); event.returnValue = '';}});
list().catch(e => status(e.message));
captureStatus();
