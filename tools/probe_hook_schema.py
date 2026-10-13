"""Temporary, expiring exact-command Hook schema probe; no payload values logged."""
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def schema(value, depth=0):
    if isinstance(value, dict) and depth < 3:
        return {k: schema(v, depth+1) for k,v in list(value.items())[:64]
                if isinstance(k,str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', k)}
    return type(value).__name__


def record(root, raw):
    try:
        config=root/'config.json'
        if config.is_symlink() or config.stat().st_size>16384:return
        c=json.loads(config.read_text(encoding='utf-8'))
        remaining=(datetime.fromisoformat(c['expires'])-datetime.now(timezone.utc)).total_seconds()
        if c.get('enabled') is not True or not 0<remaining<=900:return
        p=json.loads(raw.decode('utf-8-sig'))
        if not isinstance(p,dict) or p.get('cwd')!=c['project']:return
        if p.get('hook_event_name')!='PostToolUse' or p.get('tool_name')!='Bash':return
        args=p.get('tool_input')
        if not isinstance(args,dict):return
        cases=[k for k,v in c['commands'].items() if v==args.get('command')]
        if len(cases)!=1:return
        target=root/'schema.jsonl'
        if target.is_symlink() or (target.exists() and target.stat().st_size>=65536):return
        with target.open('a',encoding='utf-8') as out:
            out.write(json.dumps({'case':cases[0],'fields':schema(p)})+'\n')
    except (OSError,ValueError,TypeError,KeyError,RecursionError):
        pass


def main():
    raw=sys.stdin.buffer.read(512*1024+1)
    record(Path(sys.argv[1]),raw)
    result=subprocess.run([sys.executable,*sys.argv[2:]],input=raw,timeout=8)
    return result.returncode


if __name__=='__main__':raise SystemExit(main())
