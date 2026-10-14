"""Read-only installed rules, prior data and (if present) live exports."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys


def control_text(path):
    # Preserve Windows CRCRLF in previously saved control outputs. Text-mode
    # universal newline conversion would turn it into an extra blank line and
    # change an HTTP header/body boundary before the installed parser sees it.
    return path.read_bytes().decode('utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installed-root', type=Path, required=True)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--backup', type=Path, required=True)
    parser.add_argument('--run-root', type=Path, required=True)
    args = parser.parse_args()
    installed = args.installed_root.resolve(strict=True)
    sys.path.insert(0, str(installed / 'scripts'))
    import blackbox_anomalies as a
    from blackbox_launcher import build_id
    from blackbox_mcp import Reader
    assert Path(a.__file__).resolve().parent == installed / 'scripts'
    root = args.run_root.resolve(strict=True)
    manifest = json.loads((root / 'control.json').read_text(encoding='utf-8'))
    report = {'installed_build': build_id(), 'rule': a.RULE_VERSION,
              'build_matches': build_id() == build_id(Path(__file__).resolve().parents[1] / 'plugins/blackbox')}
    controls = {}
    for case, command in manifest['commands'].items():
        hit = a.detect({'tool_name': 'Bash', 'tool_input': {'command': command},
                        'tool_response': control_text(root / (case + '-control.txt'))})
        control = manifest['controls'][case]
        if 'expected_rule' in control:
            controls[case] = (hit['rule'] if hit else None) == control['expected_rule']
        else:
            controls[case] = bool(hit) == (control['exit_code'] != 0)
    report['independent_controls'] = controls
    with sqlite3.connect((args.state_dir / 'notes.sqlite3').resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.execute('ATTACH DATABASE ? AS prior', (args.backup.resolve().as_uri() + '?mode=ro',))
        report['prior_events'] = db.execute('SELECT COUNT(*) FROM prior.events').fetchone()[0]
        report['prior_events_unchanged'] = db.execute('SELECT COUNT(*) FROM (SELECT * FROM prior.events EXCEPT SELECT * FROM main.events)').fetchone()[0] == 0
        old_projects = db.execute('SELECT path,enabled FROM prior.projects ORDER BY path').fetchall()
        projects = db.execute('SELECT path,enabled FROM main.projects ORDER BY path').fetchall()
        report['project_authorization_unchanged'] = old_projects == projects
        report['through'] = db.execute('SELECT MAX(seq) FROM events').fetchone()[0]
    live = json.loads((root / 'report.json').read_text(encoding='utf-8'))
    reader = Reader(args.state_dir)
    exports = {}
    try:
        for case, items in live['cases'].items():
            for item in items:
                if not item.get('export_matches'):
                    continue
                path = f'/api/anomaly-export/{item["seq"]}?through={live["through"]}&rule={live["rule"]}'
                result = reader.call('read_view', {'path': path}).get('_meta', {}).get('blackbox', {})
                raw = result.get('body', '').encode('utf-8')
                exports[case] = result.get('status') == 200 and raw == (root / (case + '-expected.md')).read_bytes() and hashlib.sha256(raw).hexdigest() == item['export_sha256']
    finally:
        reader.names.close()
        reader.projects.close()
    report['installed_exports'] = exports
    report['installation_checks_passed'] = (report['build_matches'] and all(controls.values())
        and report['prior_events_unchanged'] and report['project_authorization_unchanged'])
    # No exports (or missing live sources) is not successful desktop acceptance.
    report['desktop_acceptance_passed'] = (report['installation_checks_passed']
        and live['passed'] and len(exports) == sum(bool(c['expected_rule']) if 'expected_rule' in c else c['exit_code'] != 0 for c in manifest['controls'].values())
        and all(exports.values()))
    (root / 'installed-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    if not report['installation_checks_passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
