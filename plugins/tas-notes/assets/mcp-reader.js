/* Local MCP Apps transport. No fetch, model-context update or chat message. */
(() => {
  let sequence=0;
  const pending=new Map();
  function rpc(method,params,options={}) {
    return new Promise((resolve,reject)=>{
      const id=++sequence;
      const timer=setTimeout(()=>{pending.delete(id);reject(new Error(options.timeoutMessage || '插件连接超时，请重新打开 Blackbox 面板。'));},options.timeoutMs || 15000);
      pending.set(id,{resolve,reject,timer,failure:options.failure});
      window.parent.postMessage({jsonrpc:'2.0',id,method,params},'*');
    });
  }
  window.addEventListener('message',event=>{
    if(event.source!==window.parent || event.data?.jsonrpc!=='2.0') return;
    const message=event.data, waiter=pending.get(message.id);
    if(!waiter) return;
    pending.delete(message.id); clearTimeout(waiter.timer);
    if(message.error) {
      const code=Number.isSafeInteger(message.error.code)?`（错误码 ${message.error.code}）`:'';
      waiter.reject(new Error((waiter.failure || '插件连接失败，请重新打开 Blackbox 面板。')+code));
    }
    else waiter.resolve(message.result);
  });
  let ready;
  function connect() {
    if(!ready) ready=rpc('ui/initialize',{appInfo:{name:'Blackbox',version:'__BLACKBOX_VERSION__'},appCapabilities:{},protocolVersion:'2026-01-26'}).then(result=>{
      window.parent.postMessage({jsonrpc:'2.0',method:'ui/notifications/initialized',params:{}},'*');
      return result;
    }).catch(error=>{ready=null;throw error;});
    return ready;
  }
  window.blackboxDownload=async(name,text)=>{
    if(typeof name!=='string' || !/^[a-zA-Z0-9][a-zA-Z0-9._-]{0,180}\.md$/.test(name) || typeof text!=='string') throw new Error('导出文件无效，请重新预览。');
    await connect();
    // Use the public MCP Apps wire format, not Codex's internal Blob handler.
    // Some desktop bridges omit downloadFile from initialize. An explicit user
    // click may request the standard method; unsupported hosts must reject it.
    const params={contents:[{type:'resource',resource:{uri:'file:///'+name,mimeType:'text/markdown',text}}]};
    const result=await rpc('ui/download-file',params,{
      timeoutMs:120000,
      failure:'宿主未接受下载，请重试或从本地浏览器阅读入口导出。',
      timeoutMessage:'尚未收到下载确认，请先检查保存提示和下载目录，避免重复下载。'
    });
    if(!result || typeof result!=='object' || result.isError) throw new Error('宿主未确认下载完成，请检查保存提示或从本地浏览器阅读入口导出。');
    return result;
  };
  window.blackboxOpenBrowser=async(snapshot,isCurrent=()=>true)=>{
    await connect();
    if(!isCurrent()) return;
    const result=await rpc('tools/call',{name:'open_browser_export',arguments:{record_id:String(snapshot.id),through:snapshot.through,rule:snapshot.rule}});
    if(!isCurrent()) return;
    const value=result?._meta?.blackbox;
    if(value?.status===409) throw new Error('本地阅读服务版本已变化，请停止并重新打开阅读服务后重试。');
    if(value?.status!==200 || typeof value.body?.url!=='string') throw new Error('本地浏览器入口未就绪，请重试。');
    const url=new URL(value.body.url),params=new URLSearchParams(url.hash.slice(1));
    if(url.protocol!=='http:' || url.hostname!=='127.0.0.1' || !url.port || url.pathname!=='/' || url.search || url.username || url.password ||
       !/^[A-Za-z0-9_-]{43}$/.test(params.get('token') || '') || params.get('record')!==String(snapshot.id) || params.get('through')!==String(snapshot.through) ||
       params.get('preview')!=='1' || [...params.keys()].length!==4) throw new Error('本地浏览器入口无效，未打开。');
    const opened=await rpc('ui/open-link',{url:url.href},{failure:'宿主未打开本地浏览器，请通过本地阅读入口继续。'});
    if(!opened || typeof opened!=='object' || opened.isError) throw new Error('宿主未确认打开浏览器，请通过本地阅读入口继续。');
  };
  window.blackboxSaveExport=async(snapshot,isCurrent=()=>true)=>{
    if(!snapshot || !/^[1-9][0-9]*$/.test(String(snapshot.id)) || !Number.isSafeInteger(snapshot.through) || snapshot.through<1 ||
       typeof snapshot.rule!=='string' || !snapshot.rule || typeof snapshot.body!=='string') throw new Error('预览无效，请重新预览。');
    // This digest came with the same private read_view response as the immutable
    // preview text. It avoids requiring WebCrypto in a sandboxed host iframe.
    const digest=snapshot.preview_sha256,bytes=new TextEncoder().encode(snapshot.body);
    if(typeof digest!=='string' || !/^[0-9a-f]{64}$/.test(digest)) throw new Error('缺少预览校验信息，请刷新插件并重新预览。');
    await connect();
    if(!isCurrent()) return;
    const result=await rpc('tools/call',{name:'save_export',arguments:{record_id:String(snapshot.id),through:snapshot.through,rule:snapshot.rule,preview_sha256:digest}},{
      timeoutMs:30000,
      failure:'插件未接受保存请求，请刷新插件并重新打开面板。',
      timeoutMessage:'尚未收到保存结果，请先检查下载文件夹，避免重复保存。'
    });
    if(!isCurrent()) return;
    const value=result?._meta?.blackbox,body=value?.body;
    if(value?.status===409) throw new Error('内容与当前预览不一致，未保存。请重新预览后再保存。');
    if(value?.status!==200) {
      const messages={downloads_unavailable:'无法找到可用的本机下载文件夹，请检查 Windows 下载文件夹设置。',export_save_failed:'保存未完成，请检查下载文件夹的可写权限和剩余空间。',export_name_exhausted:'同名副本过多，请先整理下载文件夹后再保存。'};
      throw new Error(messages[body?.error] || '保存未完成，请重新预览后重试。');
    }
    const namePattern=new RegExp('^anomaly-'+snapshot.id+'-through-'+snapshot.through+'(?: \\([1-9][0-9]*\\))?\\.md$');
    if(!body || body.sha256!==digest || body.bytes!==bytes.length || typeof body.name!=='string' || !namePattern.test(body.name) ||
       typeof body.path!=='string' || !body.path.endsWith('\\'+body.name) || /[\r\n\0]/.test(body.path)) throw new Error('未取得有效的保存凭据，请检查下载文件夹后再操作。');
    return body;
  };
  window.blackboxRequest=async(path,method='GET',body)=>{
    await connect();
    let name,args;
    if(method==='GET') {name='read_view';args={path};}
    else if(method==='POST' && path==='/api/reader-selection') {name='select_session';args=body;}
    else if(method==='POST' && path==='/api/session-management') {name='manage_session';args=body;}
    else if(method==='POST' && path==='/api/project-management') {name='manage_project';args=body;}
    else if(method==='POST' && path==='/api/cleanup') {name='cleanup';args=body;}
    else throw new Error('此面板不支持该操作。');
    const result=await rpc('tools/call',{name,arguments:args},{timeoutMs:name==='cleanup'?120000:15000});
    const value=result?._meta?.blackbox;
    if(!value || !Number.isInteger(value.status)) throw new Error('宿主未返回本地阅读数据，请更新插件后重新打开面板。');
    const headers={'Content-Type':typeof value.body==='string'?'text/markdown; charset=utf-8':'application/json'};
    if(typeof value.body==='string' && /^[0-9a-f]{64}$/.test(value.preview_sha256 || '')) headers['X-Blackbox-Preview-SHA256']=value.preview_sha256;
    return new Response(typeof value.body==='string'?value.body:JSON.stringify(value.body),{status:value.status,headers});
  };
})();
