"""Inspect only NOTES-024 plugin inventory; never starts a model turn."""
import json
import os
from pathlib import Path
from verify_live_codex import Rpc

rpc = Rpc(dict(os.environ), enable_hooks=False)
try:
    rpc.request('initialize', {'clientInfo': {'name': 'blackbox-entry-check', 'version': '0.1'},
                              'capabilities': {'experimentalApi': True}})
    rpc.send({'method': 'initialized'})
    root = Path(__file__).resolve().parent / 'entry-probe/.agents/plugins/marketplace.json'
    result = rpc.request('plugin/read', {'pluginName': 'blackbox-entry-probe', 'marketplacePath': str(root)})
    print(json.dumps({'plugin': result}, ensure_ascii=True))
    result = rpc.request('mcpServerStatus/list', {'serverName': 'blackbox_probe', 'detail': 'full'}, timeout=30)
    print(json.dumps({'mcp': result}, ensure_ascii=True))
    resource = rpc.request('mcpServer/resource/read', {'server': 'blackbox_probe',
                           'uri': 'ui://blackbox-probe/entry-v1.html'}, timeout=30)
    # Only report static probe checks, never arbitrary host resource bodies.
    encoded = json.dumps(resource, ensure_ascii=False)
    print(json.dumps({'host_resource_read': 'Blackbox 入口验证' in encoded,
                      'resource_response_keys': list(resource)}, ensure_ascii=True))
finally:
    rpc.close()
