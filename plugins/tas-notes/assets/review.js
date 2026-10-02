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
function status(message) {
  element('status').textContent = message;
  element('editor-status').textContent = message;
}
function dateLabel(stamp) {
  if (!stamp) return '时间未知';
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
    element('capture-history').hidden = !failure;
    element('capture-help').textContent = value.runtime.state === 'configured'
      ? '若预期有新记录却未出现，请刷新状态，并在 Codex 中检查项目启用、Hook 信任及最近投递。'
      : '请在 Codex 中说“检查 TAS Notes 的 Python 配置和采集状态”。已有记录仍可阅读。';
  } catch (error) {
    if (sequence !== captureSequence) return;
    element('capture-status').textContent = `状态读取失败：${error.message}`;
    element('capture-projects').replaceChildren(); element('capture-history').hidden = true;
    element('capture-help').textContent = '先保留未保存编辑，再双击桌面或开始菜单中的 TAS Notes 重新打开。';
  }
}
async function api(path, body) {
  let response;
  try { response = await fetch(path, {
    signal: AbortSignal.timeout(15000),
    method: body ? 'POST' : 'GET',
    headers: {Authorization: `Bearer ${token || ''}`, ...(body ? {'Content-Type': 'application/json'} : {})},
    ...(body ? {body: JSON.stringify(body)} : {})
  }); } catch (_) {
    throw new Error('本地服务未响应或连接中断。未保存编辑仍在当前页面，请先复制保存，再双击桌面或开始菜单中的 TAS Notes（也可让 Codex 打开）。若刚才正在保存，请重新核对版本；不能确定请求是否已落盘。');
  }
  if (!response.ok) {
    if (response.status === 409) throw new Error('已有新事件或其他人工版本。当前编辑仍在页面内，请先复制保存，再重新打开记录核对。');
    if (response.status === 401) throw new Error('缺少本地审阅凭据，请使用启动程序显示的完整链接打开。');
    if (response.status === 503) throw new Error('本地记录暂时无法读取或写入，可能被占用或磁盘不可写。请稍后重试；未保存编辑仍在页面内。');
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
    button.dataset.recordId = row.id;
    button.setAttribute('aria-current', String(selected?.id === row.id));
    const title = document.createElement('strong'); title.textContent = row.display_title || `调查 ${row.id.slice(0,8)}`;
    const meta = document.createElement('small'); meta.textContent = `${row.event_count} 个事件 · ${row.title_source === 'human' ? '人工标题' : '首条输入 / 默认名称'} · ${row.project}`;
    button.append(title, meta); button.addEventListener('click', () => openRecord(row.id).catch(e => status(e.message)));
    container.append(button);
  }
  element('list-status').textContent = rows.length ? `找到 ${rows.length} 份本地记录。` : element('query').value.trim()
    ? '没有匹配记录，请尝试其他标题或项目名称。搜索范围为最近 500 条。'
    : '暂无记录。请先为项目启用记录；安装插件本身不会授权采集。';
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
  for (const button of element('records').children) button.setAttribute('aria-current', String(button.dataset.recordId === id));
  element('empty').hidden = true; element('detail').hidden = false;
  element('record-heading').textContent = record.display_title;
  element('meta').textContent = `${record.project} · ${record.events.length} 个事件 · 仅本地`;
  element('title').value = record.display_title;
  recordSummary(record);
  element('body').value = record.revision?.body || (record.draft.length <= 200000 ? record.draft : '');
  element('body').placeholder = '在这里整理过程、依据、结果与待确认事项。';
  element('editor-help').textContent = !record.revision && record.draft.length > 200000
    ? '草稿超过编辑容量，未自动填入。请让 Codex 整理，或手动写入不超过 200000 字符的内容。'
    : '编辑后保存为独立人工版本；保存不等于确认问题已解决。';
  element('draft').textContent = record.overview;
  element('summary-prompt').hidden = true;
  element('draft-state').textContent = record.revision
    ? '正在阅读已保存的人工版本。新增草稿不会覆盖它；可点击“用当前草稿开始编辑”对照整理。'
    : record.summary_stale ? '有新增来源，旧 Agent 草稿已过期。当前展示自动摘录，请让 Codex 重新整理。'
    : record.summary ? '这是 Agent 整理草稿，引用已匹配来源，内容仍待你核对。'
    : '当前是自动摘录，尚未生成语义总结。点击“获取 Codex 整理指令”，让当前 Agent 整理。';
  renderReading(record.revision?.body || record.draft);
  element('export').disabled = !record.revision;
  element('export-editor').disabled = !record.revision;
  const events = element('events'); events.replaceChildren();
  for (const event of record.events) {
    const detail = document.createElement('details'); detail.id = `source-${event.seq}`;
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
  if (!element('title').value.trim() || !element('body').value.trim()) {
    status('标题和正文不能为空。请先填写内容再保存。'); return;
  }
  const snapshot = {id: selected.id,
    title: element('title').value, body: element('body').value,
    source_seq: selected.source_seq, expected_revision: selected.revision?.number || 0};
  busy = true; ++openSequence; element('save').disabled = true;
  try {
    const result = await (await api('/api/revisions', snapshot)).json();
    selected.revision = {...snapshot, ...result};
    recordSummary(selected);
    renderReading(snapshot.body);
    element('draft-state').textContent = '正在阅读已保存的人工版本；它不会被新增草稿覆盖。';
    element('record-heading').textContent = snapshot.title;
    for (const button of element('records').children) {
      if (button.dataset.recordId === selected.id) button.querySelector('strong').textContent = snapshot.title;
    }
    dirty = element('title').value !== snapshot.title || element('body').value !== snapshot.body;
    element('export').disabled = false;
    element('export-editor').disabled = false;
    status(`人工版本 ${result.number} 已保存。${dirty ? '保存期间的新增编辑仍未保存。' : '尚未发布。'}`);
  } finally {busy = false; element('save').disabled = false;}
}
async function exportSaved() {
  if (!selected?.revision) return;
  // Selection or revision can change during the request; pin content and name together.
  const snapshot = {recordId: selected.id, revisionId: selected.revision.id, number: selected.revision.number};
  const response = await api(`/api/export/${encodeURIComponent(snapshot.revisionId)}`);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a'); link.href = url;
  link.download = `investigation-${snapshot.recordId}-v${snapshot.number}.md`;
  link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  status(`已请求下载人工版本 ${snapshot.number}；未包含未保存编辑。没有上传或 Git 推送。`);
}
// Only text nodes and locally validated source buttons; never execute source HTML/Markdown.
function renderReading(body) {
  const target = element('reading'); target.replaceChildren();
  const available = new Set(selected.events.map(event => event.seq));
  for (const line of body.split('\n')) {
    if (!line.trim()) continue;
    const heading = /^(#{1,3}) (.*)$/.exec(line);
    const node = document.createElement(heading ? (heading[1].length < 3 ? 'h3' : 'h4') : 'p');
    const value = heading ? heading[2] : line;
    let start = 0;
    for (const match of value.matchAll(/\[来源 #(\d+)\]/g)) {
      node.append(document.createTextNode(value.slice(start, match.index)));
      const seq = Number(match[1]);
      if (available.has(seq)) {
        const button = document.createElement('button'); button.className = 'citation'; button.textContent = `来源 #${seq}`;
        button.addEventListener('click', () => {
          const source = element(`source-${seq}`); source.open = true;
          source.parentElement.closest('details').open = true;
          source.scrollIntoView({behavior: 'smooth', block: 'center'});
        });
        node.append(button);
      } else node.append(document.createTextNode(match[0]));
      start = match.index + match[0].length;
    }
    node.append(document.createTextNode(value.slice(start))); target.append(node);
  }
}
element('summary-request').addEventListener('click', () => {
  if (!selected) return;
  const prompt = element('summary-prompt');
  prompt.value = `请使用 TAS Notes 审阅技能，为记录 ${selected.id}（项目 ${selected.project}）生成或更新有来源的过程总结。保留失败、纠正和未知，不覆盖人工版本。整理会读取这份本地记录到当前 Codex 上下文。`;
  prompt.hidden = false; prompt.focus(); prompt.select();
  status('复制上方指令发送给 Codex。网页本身不会启动模型；整理完成后重新打开记录。');
});
element('use-draft').addEventListener('click', () => {
  if (!selected || busy) return;
  if ((dirty || selected.revision) && !confirm('用当前草稿替换编辑框内容？已保存的人工版本仍保留；未保存编辑会丢失。')) return;
  if (selected.draft.length > 200000) {status('草稿过长，请先让 Codex 整理。'); return;}
  element('body').value = selected.draft; dirty = true; ++editSequence;
  element('editor').open = true;
  status('草稿已放入编辑框，尚未保存。');
});
for (const id of ['title', 'body']) element(id).addEventListener('input', () => {dirty = true; ++editSequence;});
for (const [id, action] of [['search', list], ['refresh', list], ['save', save], ['export', exportSaved], ['export-editor', exportSaved]]) {
  element(id).addEventListener('click', () => action().catch(e => status(e.message)));
}
element('refresh-capture').addEventListener('click', captureStatus);
element('edit-record').addEventListener('click', () => {
  element('editor').open = true; element('editor').scrollIntoView({behavior: 'smooth', block: 'start'});
  element('title').focus();
});
element('query').addEventListener('keydown', event => {if (event.key === 'Enter') list().catch(e => status(e.message));});
window.addEventListener('beforeunload', event => {if (dirty) {event.preventDefault(); event.returnValue = '';}});
list().catch(e => status(e.message));
captureStatus();
