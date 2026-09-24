'use strict';
const element = id => document.getElementById(id);
const token = new URLSearchParams(location.hash.slice(1)).get('token');
let selected = null;
let dirty = false;
let busy = false;
let openSequence = 0;
let editSequence = 0;
function status(message) { element('status').textContent = message; }
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
  const rows = await (await api(`/api/records?q=${encodeURIComponent(element('query').value)}`)).json();
  const container = element('records'); container.replaceChildren();
  for (const row of rows) {
    const button = document.createElement('button'); button.className = 'record';
    const title = document.createElement('strong'); title.textContent = row.title || `调查 ${row.id.slice(0,8)}`;
    const meta = document.createElement('small'); meta.textContent = `${row.event_count} 个事件 · ${row.project}`;
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
  element('record-heading').textContent = record.revision?.title || `调查 ${record.id.slice(0,8)}`;
  element('meta').textContent = `${record.project} · ${record.events.length} 个事件 · 仅本地`;
  element('title').value = record.revision?.title || '调查记录';
  element('body').value = record.revision?.body || (record.draft.length <= 200000 ? record.draft : '来源材料超过人工版本容量。请参考下方草稿与来源，整理为不超过 200000 字符的记录。');
  element('draft').textContent = record.draft;
  element('export').disabled = !record.revision;
  const events = element('events'); events.replaceChildren();
  for (const event of record.events) {
    const pre = document.createElement('pre');
    pre.textContent = `#${event.seq} ${event.kind} · ${event.captured}\nSHA-256 ${event.digest}\n${JSON.stringify(JSON.parse(event.content), null, 2)}`;
    events.append(pre);
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
element('query').addEventListener('keydown', event => {if (event.key === 'Enter') list().catch(e => status(e.message));});
window.addEventListener('beforeunload', event => {if (dirty) {event.preventDefault(); event.returnValue = '';}});
list().catch(e => status(e.message));
