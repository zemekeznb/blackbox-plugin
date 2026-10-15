"""Build a local, deterministic Alpha candidate using only Python's standard library.

This does not publish, install, grant a license, or attest real-host acceptance.
The file allowlist is deliberately independent of git tracking and ignore rules.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
import zipfile


PLUGIN = 'plugins/blackbox/'
SCRIPT_NAMES = (
    'open_notes.py', 'blackbox.py', 'blackbox_anomalies.py', 'blackbox_backup.py',
    'blackbox_business_errors.py', 'blackbox_capture_probe.py', 'blackbox_codex.py',
    'blackbox_core.py', 'blackbox_curl_errors.py', 'blackbox_curl_result.py',
    'blackbox_diagnostics.py', 'blackbox_http_errors.py', 'blackbox_launcher.py',
    'blackbox_mcp.py', 'blackbox_shell_errors.py', 'blackbox_sse_errors.py',
    'blackbox_admission.py', 'blackbox_preflight.py', 'blackbox_sessions.py', 'blackbox_cleanup.py',
    'blackbox_summary.py', 'blackbox_transport_errors.py', 'blackbox_web.py', 'blackbox_install.py', 'blackbox_compat.py',
    'launch.cmd', 'launch-legacy.cmd', 'mcp.cmd', 'blackbox_runtime.py', 'blackbox_runtime.ps1', 'install_shortcuts.ps1',
)
ASSET_NAMES = ('blackbox-mark.svg', 'anomalies.html', 'anomalies.js', 'mcp-reader.js', 'review.css',
               'review.html', 'review.js')
TEST_HELPERS = (
    'tools/build_alpha.py', 'tools/verify_alpha_candidate.py', 'tools/verify_windows_install.py', 'tools/verify_install_flow.py', 'tools/verify_brand_migration.py', 'tools/verify_git_install.py', 'tools/bootstrap_blackbox.py',
    'tools/bash_error_cases.py', 'tools/hook_shape_probe.py',
    'tools/probe_hook_schema.py', 'tools/probe_result_api.py', 'tools/trace_hook_entry.py',
    'tools/verify_acceptance.py', 'tools/verify_bash_coverage.py',
    'tools/verify_mcp_coverage.py', 'tools/verify_shell_formats.py',
    'tools/verify_shell_installation.py', 'tools/entry-probe/plugin/server.py',
    'tools/entry-probe/plugin/probe.html', 'tools/error-probe/plugin/server.py',
    'tools/verify_batch_coverage.py', 'tools/verify_curl_errors.py',
    'tools/verify_quality_errors.py',
)
REQUIRED_FILES = (
    PLUGIN + '.codex-plugin/plugin.json', PLUGIN + '.mcp.json',
    PLUGIN + 'hooks/hooks.json', PLUGIN + 'skills/blackbox-review/SKILL.md',
    '.agents/plugins/marketplace.json', 'release/ALPHA-GUIDE.md', 'release/INSTALL.md', 'release/INSTALL-CODEX.md', 'install.ps1',
    PLUGIN + 'skills/setup/SKILL.md',
) + tuple(PLUGIN + 'scripts/' + name for name in SCRIPT_NAMES) + tuple(
    PLUGIN + 'assets/' + name for name in ASSET_NAMES) + TEST_HELPERS
ALLOWED_GLOBS = (
    PLUGIN + 'scripts/*.py', PLUGIN + 'assets/*.js', PLUGIN + 'assets/*.css',
    PLUGIN + 'assets/*.html', 'tests/test_*.py', 'tests/test_*.cjs',
)
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
VERSION_PATTERN = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?\Z')
# Deliberately limited, high-confidence patterns. Never print the matched value.
SECRET_PATTERNS = (
    ('private_key', re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----')),
    ('openai_key', re.compile(rb'\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{24,}\b')),
    ('github_token', re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b')),
    ('aws_access_key', re.compile(rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b')),
    ('slack_token', re.compile(rb'\bxox[baprs]-[A-Za-z0-9-]{24,}\b')),
    ('literal_bearer', re.compile(rb'(?i)\bBearer[ \t]+[A-Za-z0-9._~+/-]{24,}')),
)


class BuildError(Exception):
    """A concise candidate-build failure safe to show without source contents."""


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def _linked(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))


def checked_file(root, relative):
    """Refuse symlinks/junctions in every component, including links inside root."""
    rel = PurePosixPath(relative)
    if rel.is_absolute() or '..' in rel.parts or '\\' in relative:
        raise BuildError(f'Unsafe archive path: {relative}')
    current = root
    for part in rel.parts:
        current = current / part
        try:
            if _linked(current):
                raise BuildError(f'Symlink or reparse point is not allowed: {relative}')
        except FileNotFoundError as exc:
            raise BuildError(f'Missing required file: {relative}') from exc
    if not current.is_file() or not current.resolve().is_relative_to(root):
        raise BuildError(f'Not a regular file inside source root: {relative}')
    return current


def collect_payload(root):
    names = set(REQUIRED_FILES)
    for pattern in ALLOWED_GLOBS:
        # Check the parent first: glob must not enter an external junction.
        parent = root / str(PurePosixPath(pattern).parent)
        if parent.exists() or parent.is_symlink():
            relative_parent = parent.relative_to(root)
            cursor = root
            for part in relative_parent.parts:
                cursor /= part
                if _linked(cursor):
                    raise BuildError(f'Symlink or reparse point is not allowed: {relative_parent.as_posix()}')
            names.update(path.relative_to(root).as_posix() for path in root.glob(pattern))
    if (root / 'LICENSE').exists() or (root / 'LICENSE').is_symlink():
        names.add('LICENSE')
    result = {}
    for name in sorted(names):
        data = checked_file(root, name).read_bytes()
        for label, pattern in SECRET_PATTERNS:
            if pattern.search(data):
                raise BuildError(f'Possible secret ({label}) in {name}; review locally before packaging. Value omitted.')
        result[name] = data
    if not any(name.startswith('tests/test_') and name.endswith('.py') for name in result):
        raise BuildError('Missing offline tests: tests/test_*.py')
    return result


def source_identity(root):
    """Record git evidence without exposing tracked paths or treating errors as clean."""
    def git(*args):
        return subprocess.run(['git', '-C', str(root), *args], check=True,
                              capture_output=True, timeout=15).stdout.decode('utf-8').strip()
    try:
        toplevel = Path(git('rev-parse', '--show-toplevel')).resolve()
        if toplevel != root:
            return {'commit': None, 'dirty': None, 'status': 'not_repository_root'}
        commit = git('rev-parse', '--verify', 'HEAD')
        if not re.fullmatch(r'[a-fA-F0-9]{40,64}', commit):
            raise ValueError('Invalid git object id')
        dirty = bool(git('status', '--porcelain', '--untracked-files=all'))
        return {'commit': commit, 'dirty': dirty, 'status': 'available'}
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError):
        return {'commit': None, 'dirty': None, 'status': 'unavailable'}


def check_install_entrypoints(payload, version):
    """Keep fixed installation targets aligned without executing the bootstrap."""
    try:
        tree = ast.parse(payload['tools/bootstrap_blackbox.py'].decode('utf-8-sig'))
        instructions = payload['release/INSTALL-CODEX.md'].decode('utf-8-sig')
    except (KeyError, UnicodeError, SyntaxError) as exc:
        raise BuildError('Installation entrypoints must contain valid Python and UTF-8 instructions.') from exc
    assignments = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == 'VERSION' for target in node.targets):
            assignments.append(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == 'VERSION':
            assignments.append(node.value)
    if (len(assignments) != 1 or not isinstance(assignments[0], ast.Constant)
            or assignments[0].value != version):
        raise BuildError('bootstrap_blackbox.py VERSION must be one literal matching plugin.json version.')
    # Check actual download targets, not translated prose or README contents.
    # Both URLs identify the same tagged source; an unpinned or mixed target fails.
    refs = re.findall(
        r'https://(?:raw\.githubusercontent\.com/zemekeznb/blackbox-plugin/'
        r'|github\.com/zemekeznb/blackbox-plugin/blob/)'
        r'([^/\s`<>]+)/tools/bootstrap_blackbox\.py\b', instructions)
    if not refs or set(refs) != {'v' + version}:
        raise BuildError('INSTALL-CODEX.md bootstrap links must use the fixed tag matching plugin.json version.')


def _write_atomic(path, data):
    if path.is_symlink() or (path.exists() and _linked(path)):
        raise BuildError(f'Refusing linked output: {path.name}')
    handle, temporary = tempfile.mkstemp(prefix='.alpha-', dir=path.parent)
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def build(source_root, output_dir=None, *, for_release=False):
    root_input = Path(source_root).absolute()
    if not root_input.is_dir():
        raise BuildError('Source root does not exist or is not a directory.')
    if _linked(root_input):
        raise BuildError('Source root must not be a symlink or reparse point.')
    root = root_input.resolve()
    payload = collect_payload(root)
    try:
        metadata = json.loads(payload[PLUGIN + '.codex-plugin/plugin.json'].decode('utf-8-sig'))
        version = metadata['version']
    except (KeyError, ValueError, UnicodeError, TypeError) as exc:
        raise BuildError('plugin.json must contain a valid JSON object with a version.') from exc
    if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
        raise BuildError('plugin.json version must be a safe semantic version (for example 0.4.0-alpha.1).')
    check_install_entrypoints(payload, version)
    if 'LICENSE' in payload and not payload['LICENSE'].strip():
        raise BuildError('LICENSE is empty; supply the approved license or omit it for a local candidate.')
    license_pending = 'LICENSE' not in payload
    source = source_identity(root)
    blockers = ['real_host_acceptance_not_attested', 'publication_not_authorized_by_builder']
    if license_pending:
        blockers.append('license_pending')
    if source['dirty'] is not False:
        blockers.append('source_dirty_or_unknown')
    if for_release and license_pending:
        raise BuildError('Release build blocked: LICENSE is pending. A local candidate is allowed without --for-release.')
    if for_release and source['dirty'] is not False:
        raise BuildError('Release build blocked: source must have a known clean Git commit.')
    manifest = {
        'schema_version': 1, 'product': 'Blackbox Plugin', 'version': version,
        'artifact_kind': 'alpha_candidate', 'license_pending': license_pending,
        'publish_ready': False, 'release_blockers': blockers, 'source': source,
        'secret_scan': {'scope': 'allowlisted files only', 'coverage': 'limited_known_patterns'},
        'offline_tests_included': True,
        'files': {name: {'sha256': sha256(data), 'size': len(data)} for name, data in payload.items()},
    }
    manifest_data = json_bytes(manifest)
    payload['release-manifest.json'] = manifest_data
    payload['SHA256SUMS'] = ''.join(f'{sha256(payload[name])}  {name}\n' for name in sorted(payload)).encode('utf-8')
    archive = io.BytesIO()
    # ZIP_STORED avoids variation between zlib implementations; timestamps, modes,
    # ordering and metadata are fixed, so identical inputs yield identical bytes.
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_STORED) as bundle:
        for name in sorted(payload):
            info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            bundle.writestr(info, payload[name])
    output = Path(output_dir) if output_dir else root / 'dist'
    # Output is caller-selected but must not travel through a junction/symlink.
    output = output.absolute()
    for part in (output, *output.parents):
        if part.exists() and _linked(part):
            raise BuildError('Output directory must not contain symlinks or reparse points.')
    output.mkdir(parents=True, exist_ok=True)
    stem = f'blackbox-plugin-{version}-candidate'
    zip_path = output / (stem + '.zip')
    data = archive.getvalue()
    _write_atomic(zip_path, data)
    _write_atomic(output / (stem + '.zip.sha256'), f'{sha256(data)}  {zip_path.name}\n'.encode('ascii'))
    _write_atomic(output / (stem + '.release-manifest.json'), manifest_data)
    return {'archive': str(zip_path), 'sha256': sha256(data), 'version': version,
            'license_pending': license_pending, 'publish_ready': False,
            'source': source, 'file_count': len(payload)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output-dir', type=Path, help='Default: SOURCE_ROOT/dist')
    parser.add_argument('--for-release', action='store_true',
                        help='Require an approved LICENSE and clean Git source; does not publish or attest acceptance')
    args = parser.parse_args(argv)
    try:
        result = build(args.source_root, args.output_dir, for_release=args.for_release)
    except (BuildError, OSError) as exc:
        print(f'Alpha build failed: {exc}', file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
