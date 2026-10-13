"""Read-only installed reader checks; never creates/resumes a model turn."""
import hashlib, json, os, sys
from pathlib import Path
from verify_live_codex import Rpc
from tas_notes_launcher import build_id
from tas_notes_mcp import URI, RESOURCE_URIS, resource_html
rpc=Rpc(dict(os.environ),enable_hooks=False)
try:
    rpc.request('initialize',{'clientInfo':{'name':'blackbox-reader-check','version':'1'},'capabilities':{'experimentalApi':True}})
    rpc.send({'method':'initialized'})
    state=rpc.request('mcpServerStatus/list',{'serverName':'blackbox_reader','detail':'full'},timeout=30)
    encoded=json.dumps(state)
    print(json.dumps({'mcp_reader_registered':'open_reader' in encoded,'app_only_metadata':'visibility' in encoded,'server_count':len(state.get('data',[]))},ensure_ascii=True))
    for uri in RESOURCE_URIS:
        resource=rpc.request('mcpServer/resource/read',{'server':'blackbox_reader','uri':uri},timeout=30)
        content=resource['contents'][0]
        matches=content['uri']==uri and content['text']==resource_html()
        print(json.dumps({'scope':'fresh_app_server_not_desktop','uri':uri,'installed_html_matches_source':matches,
                          'html_sha256':hashlib.sha256(content['text'].encode()).hexdigest(),'source_build':build_id()}))
        if not matches: raise RuntimeError('Installed reader differs from source')
    hooks=rpc.request('hooks/list',{'cwds':[str(Path(__file__).resolve().parents[1])]})
    # Hook inventory contains commands/paths only, never recording bodies.
    def matching(value):
        if isinstance(value,dict):
            if 'command' in value and 'tas-notes' in str(value.get('command')): yield value
            else:
                for child in value.values(): yield from matching(child)
        elif isinstance(value,list):
            for child in value: yield from matching(child)
    items=list(matching(hooks))
    print(json.dumps({'hook_count':len(items),'all_enabled_trusted':all(x.get('enabled') and x.get('trustStatus')=='trusted' for x in items)},ensure_ascii=True))
finally: rpc.close()
