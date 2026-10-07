'use strict';
const element = id => document.getElementById(id);
const token = new URLSearchParams(location.hash.slice(1)).get('token') || '';
element('anomalies-home').href = './#' + new URLSearchParams({token}).toString();
let selected = null, selectedRevision = null, openSequence = 0, listSequence = 0, captureSequence = 0;
function status(message) { element('status').textContent = message; }
function dateLabel(stamp) {
  const date = stamp ? new Date(stamp) : null;
  return !date || Number.isNaN(date.getTime()) ? '时间未知' : date.toLocaleString();
}
function block(parent, tag, text) {
  const node = document.createElement(tag); node.textContent = text; parent.append(node); return node;
}
async function api(path) {
  let response;
  try { response = await fetch(path, {signal: AbortSignal.timeout(15000), headers: {Authorization: `Bearer ${token}`}}); }
  catch (_) {throw new Error('本地服务连接中断。请双击桌面或开始菜单中的 Blackbox Plugin 重新打开。');}
  if (!response.ok) throw new Error(response.status === 401 ? '本地凭据失效，请通过 Blackbox Plugin 入口重新打开。'
    : response.status === 503 ? '本地记录暂时无法读取，请稍后重试。' : '记录读取失败，请刷新列表后重试。');
  return response;
}
async function captureStatus() {
  const sequence = ++captureSequence;
  try {
    const value = await (await api('/api/status')).json();
    if (sequence !== captureSequence) return;
    const runtime = {missing:'Python 尚未配置', invalid:'Python 配置无效', target_missing:'配置中的 Python 已不存在', configured:'Python 路径已配置（未做执行检查）'};
    element('capture-status').textContent = `${runtime[value.runtime?.state] || 'Python 配置未知'} · ${value.record_count ?? 0} 份本地记录`;
    const projects = element('capture-projects'); projects.replaceChildren();
    for (const project of value.projects || []) {
      block(projects, 'p', `${project.enabled ? '已启用' : '已停用'} · ${project.path} · ${project.last_recorded_event ? '最近落盘 '+dateLabel(project.last_recorded_event.captured) : '尚未收到记录'}`);
      if(project.last_capture_error) block(projects, 'p', `该项目的历史采集缺口：${project.last_capture_error.code} · ${dateLabel(project.last_capture_error.time)}。标记保留，不代表当前仍然故障。`).className='notice';
    }
    const failure = value.last_capture_error;
    element('capture-error').textContent = failure ? `历史采集缺口标记：${failure.code} · ${dateLabel(failure.time)}。不代表当前或特定项目仍然故障。` : '没有已保存的失败标记；不代表采集没有缺口。';
  } catch (error) {
    if (sequence === captureSequence) element('capture-status').textContent = `状态读取失败：${error.message}`;
  }
}
async function list() {
  const sequence = ++listSequence;
  let rows;
  try {rows = await (await api(`/api/records?q=${encodeURIComponent(element('query').value)}`)).json();}
  catch(error) {if(sequence===listSequence) throw error; return;}
  if (sequence !== listSequence) return;
  const container = element('records'); container.replaceChildren();
  for (const row of rows) {
    const button = document.createElement('button'); button.className = 'record'; button.dataset.recordId = row.id;
    button.setAttribute('aria-current', String(selected?.id === row.id));
    block(button, 'strong', row.display_title || `会话 ${row.id.slice(0,8)}`);
    block(button, 'small', `${row.event_count} 个事件 · ${row.title_source === 'human' ? '历史人工标题' : '首条输入 / 默认名称'} · ${row.project}`);
    button.addEventListener('click', () => openRecord(row.id).catch(e => status(e.message))); container.append(button);
  }
  element('list-status').textContent = rows.length ? `找到 ${rows.length} 份本地记录。`
    : element('query').value.trim() ? '没有匹配记录，请尝试其他标题或项目名称。搜索范围为最近 500 条。'
    : '暂无记录。安装插件本身不会授权采集。';
}
function selectRevision(id) {
  if (!selected) return;
  selectedRevision = selected.revisions.find(revision => revision.id === id) || null;
  element('export').disabled = !selectedRevision;
  if (selectedRevision) {
    element('reading-state').textContent = `历史人工版本 ${selectedRevision.number} · ${dateLabel(selectedRevision.created)} · 来源截至 #${selectedRevision.source_seq}。保存并不等于验收通过；新增事件不会改写这个版本。`;
    element('record-heading').textContent = selectedRevision.title;
    renderReading(selectedRevision.body);
  } else {
    element('reading-state').textContent = '当前事件的自动摘录；不是根因分析、修复结论或新增模型总结。完整事件和历史总结可在下方展开。';
    element('record-heading').textContent = selected.display_title;
    renderReading(selected.overview || '暂无可读摘录，请展开来源事件。');
  }
}
function historicalSummary(content) {
  try {
    const sections = typeof content === 'string' ? JSON.parse(content) : content;
    if (!Array.isArray(sections)) return typeof content === 'string' ? content : JSON.stringify(content, null, 2);
    return sections.map(section => `${section.heading}\n${(section.items || []).map(item => `${item.text}\n${(item.sources || []).map(ref => `[来源 #${ref.seq}] ${ref.quote}`).join('\n')}`).join('\n\n')}`).join('\n\n');
  } catch (_) {return String(content ?? '没有保存正文');}
}
async function openRecord(id) {
  const sequence = ++openSequence;
  let record;
  try {record = await (await api(`/api/record/${encodeURIComponent(id)}`)).json();}
  catch(error) {if(sequence===openSequence) throw error; return;}
  if (sequence !== openSequence) return;
  selected = record;
  selected.revisions = record.revisions || (record.revision ? [record.revision] : []);
  selected.revisions.sort((a,b) => b.number-a.number);
  for (const button of element('records').children) button.setAttribute('aria-current', String(button.dataset.recordId === id));
  element('empty').hidden = true; element('detail').hidden = false;
  element('meta').textContent = `${record.project} · ${record.events.length} 个事件 · ${selected.revisions.length} 个人工版本 · 只读归档`;
  const selector = element('revision'); selector.replaceChildren();
  const overview = block(selector, 'option', '当前事件自动摘录'); overview.value = '';
  for (const revision of selected.revisions) {
    const option = block(selector, 'option', `人工版本 ${revision.number} · ${dateLabel(revision.created)} · ${revision.title}`); option.value = revision.id;
  }
  selector.value = selected.revisions[0]?.id || '';
  selectRevision(selector.value);
  const events = element('events'); events.replaceChildren();
  const labels = {SessionStart:'会话开始',UserPromptSubmit:'用户输入',PostToolUse:'工具操作',Stop:'Agent 最后陈述',PreCompact:'压缩前',PostCompact:'压缩后',Interrupt:'用户中断',SessionEnd:'会话结束'};
  for (const event of record.events) {
    const detail = document.createElement('details'); detail.id = `source-${event.seq}`;
    block(detail, 'summary', `#${event.seq} ${labels[event.kind] || event.kind} · ${dateLabel(event.captured)}`);
    block(detail, 'pre', `SHA-256 ${event.digest}\n${JSON.stringify(JSON.parse(event.content),null,2)}`); events.append(detail);
  }
  const summaries = element('summaries'); summaries.replaceChildren();
  const allSummaries = record.summaries || (record.summary ? [record.summary] : []);
  if (!allSummaries.length) block(summaries, 'p', '没有已保存的历史模型总结。');
  for (const summary of allSummaries) {
    const detail = document.createElement('details');
    block(detail, 'summary', `历史模型总结 · ${dateLabel(summary.created)} · 来源截至 #${summary.source_seq}`);
    block(detail, 'p', '历史解释并非原始观察；引用匹配不证明解释正确。保存后新增的事件不在此总结内。');
    block(detail, 'pre', historicalSummary(summary.content)); summaries.append(detail);
  }
  const captureGap=record.events.some(event=>JSON.parse(event.content)._coverage?.capture_gap_before);
  status((captureGap ? '该会话存在已知采集缺口，历史内容不能证明完整过程。' : '')+'历史来源、模型总结和人工版本分别保留，只读查看。页面不会执行来源中的命令或 HTML。');
}
async function exportSaved() {
  if (!selected || !selectedRevision) return;
  const snapshot = {recordId:selected.id, revisionId:selectedRevision.id, number:selectedRevision.number};
  const response = await api(`/api/export/${encodeURIComponent(snapshot.revisionId)}`);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a'); link.href = url;
  link.download = `investigation-${snapshot.recordId}-v${snapshot.number}.md`;
  link.click(); setTimeout(() => URL.revokeObjectURL(url),1000);
  status(`已请求下载历史人工版本 ${snapshot.number}。新增事件不会加入该版本，没有上传。`);
}
// Deliberately small Markdown subset. All source content stays in text nodes.
// No raw HTML, links, images or embedded resources are executed or requested.
function renderReading(body) {
  const target = element('reading'); target.replaceChildren();
  const available = new Set(selected.events.map(event => event.seq));
  function inline(parent, value) {
    const tokens = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\n]+\*|\[来源 #\d+\])/g;
    let start = 0;
    for (const match of value.matchAll(tokens)) {
      parent.append(document.createTextNode(value.slice(start, match.index)));
      const token = match[0], ref = /^\[来源 #(\d+)\]$/.exec(token);
      if (ref && available.has(Number(ref[1]))) {
        const button = document.createElement('button'); button.className = 'citation'; button.textContent = `来源 #${ref[1]}`;
        button.addEventListener('click', () => {
          const source = element(`source-${ref[1]}`); source.open = true;
          source.parentElement.closest('details').open = true;
          source.scrollIntoView({behavior: 'smooth', block: 'center'});
        });
        parent.append(button);
      } else if (ref) parent.append(document.createTextNode(token));
      else {
        const strong = token.startsWith('**');
        const node = document.createElement(token.startsWith('`') ? 'code' : strong ? 'strong' : 'em');
        node.textContent = token.slice(strong ? 2 : 1, strong ? -2 : -1); parent.append(node);
      }
      start = match.index + token.length;
    }
    parent.append(document.createTextNode(value.slice(start)));
  }
  let list = null, listKind = null, quote = null, code = null, codeLines = [];
  for (const line of body.replace(/\r\n/g, '\n').split('\n')) {
    if (/^\s*```/.test(line)) {
      list = null; quote = null;
      if (code) {code.textContent = codeLines.join('\n'); code = null; codeLines = [];}
      else {const pre = document.createElement('pre'); code = document.createElement('code'); pre.append(code); target.append(pre);}
      continue;
    }
    if (code) {codeLines.push(line); continue;}
    if (!line.trim()) {list = null; quote = null; continue;}
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    const bullet = /^\s*(?:([-+*])\s+|(\d+)[.)]\s+)(.*)$/.exec(line);
    const cited = /^>\s?(.*)$/.exec(line);
    if (heading) {
      list = null; quote = null;
      const node = document.createElement(heading[1].length <= 2 ? 'h3' : 'h4'); inline(node, heading[2]); target.append(node);
    } else if (bullet) {
      quote = null; const kind = bullet[2] ? 'ol' : 'ul';
      if (!list || listKind !== kind) {list = document.createElement(kind); listKind = kind; if (kind === 'ol') list.setAttribute('start', bullet[2]); target.append(list);}
      const node = document.createElement('li'); inline(node, bullet[3]); list.append(node);
    } else if (cited) {
      list = null;
      if (!quote) {quote = document.createElement('blockquote'); target.append(quote);}
      const node = document.createElement('p'); inline(node, cited[1]); quote.append(node);
    } else {
      list = null; quote = null;
      const node = document.createElement(/^\s*(---+|\*\*\*+)\s*$/.test(line) ? 'hr' : 'p');
      if (node.tagName !== 'HR') inline(node,line); target.append(node);
    }
  }
  if (code) code.textContent = codeLines.join('\n');
}
element('revision').addEventListener('change', () => selectRevision(element('revision').value));
for (const [id,action] of [['search',list],['refresh',list],['export',exportSaved]]) {
  element(id).addEventListener('click', () => action().catch(e => status(e.message)));
}
element('refresh-capture').addEventListener('click', captureStatus);
element('query').addEventListener('keydown', event => {if(event.key==='Enter') list().catch(e=>status(e.message));});
if (location.protocol === 'file:') {
  element('file-preview').hidden = false;
  element('capture-status').textContent = '文件预览模式，未连接本地记录服务。';
  element('list-status').textContent = '请使用 Blackbox Plugin 启动入口读取记录。';
  for (const id of ['query','search','refresh','refresh-capture','revision','export']) element(id).disabled = true;
  status('页面样式可预览；记录读取和下载需要本地服务。');
} else {
  list().catch(e=>status(e.message)); captureStatus();
  const initialRecord = new URLSearchParams(location.hash.slice(1)).get('record');
  if(initialRecord) openRecord(initialRecord).catch(e=>status(e.message));
}
