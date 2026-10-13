"""Read-only audit of actual NOTES-046 MCP probe deliveries; never inject events."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/tas-notes/scripts'))
from tas_notes_core import Store
from tas_notes_anomalies import detect

TOOL = 'mcp__blackbox_error_probe__run_case'
MARKERS = {'success': 'NOTES046_SUCCESS', 'error': 'NOTES046_TOOL_ERROR',
           'log_success': 'NOTES046_LOG_ONLY', 'json_error': 'NOTES046_JSON_ERROR'}


def assess(rows, success_count=1):
    expected = Counter({'success': success_count, 'error': 1, 'log_success': 1, 'json_error': 1})
    received = Counter()
    events = []
    for row in rows:
        if row['kind'] != 'PostToolUse':
            continue
        content = json.loads(row['content'])
        if content.get('tool_name') != TOOL:
            continue
        args = content.get('tool_input')
        if not isinstance(args, dict) or set(args) != {'case'}:
            continue
        case = args['case']
        if case not in (*MARKERS, 'protocol_error'):
            continue
        received[case] += 1
        result = content.get('tool_response')
        blocks = result.get('content') if isinstance(result, dict) else None
        texts = [b.get('text', '') for b in blocks if isinstance(b, dict) and b.get('type') == 'text'] if isinstance(blocks, list) else []
        expected_error = case in ('error', 'json_error')
        hit = detect(content)
        events.append({'seq': row['seq'], 'case': case, 'digest': row['digest'],
                       'marker_matches': case in MARKERS and any(isinstance(t, str) and MARKERS[case] in t for t in texts),
                       'flag_matches': isinstance(result, dict) and result.get('isError') is expected_error,
                       'detected': hit is not None,
                       'detection_matches': bool(hit) == expected_error})
    checks = {'expected_tool_results_delivered': all(received[c] == n for c, n in expected.items()),
              'saved_results_match': all(e['marker_matches'] and e['flag_matches'] and e['detection_matches']
                                         for e in events if e['case'] in MARKERS)}
    return {'checks': checks, 'passed': all(checks.values()),
            'missing_cases': [c for c in expected if received[c] < expected[c]],
            'protocol_error_delivery_count': received['protocol_error'], 'events': events,
            'evidence_scope': 'Saved Hook results only; independent host outcomes must be recorded separately.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--turn-id', required=True)
    parser.add_argument('--after', type=int, required=True)
    parser.add_argument('--success-count', type=int, default=1)
    args = parser.parse_args()
    if args.after < 0 or not 1 <= args.success_count <= 10:
        parser.error('invalid bounded audit parameters')
    store = Store(args.state_dir, read_only=True)
    with store.connect() as db:
        through = db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
        rows = db.execute('''SELECT e.* FROM events e JOIN investigations i ON i.id=e.investigation
            WHERE i.project=? AND e.seq>? AND e.seq<=? ORDER BY e.seq LIMIT 1001''',
            (str(args.project.resolve(strict=True)), args.after, through)).fetchall()
    if len(rows) > 1000:
        raise RuntimeError('audit window exceeds 1000 events')
    selected = [r for r in rows if json.loads(r['content']).get('turn_id') == args.turn_id]
    report = assess(selected, args.success_count)
    report.update(after=args.after, through=through,
                  capture_gap_events=[r['seq'] for r in selected if json.loads(r['content']).get('_coverage', {}).get('capture_gap_before')])
    print(json.dumps(report, ensure_ascii=True, indent=2))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
