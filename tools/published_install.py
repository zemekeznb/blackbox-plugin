"""Check or advance public installation links after anonymous release verification.

Candidate installation files stay pinned to their own source version. Only the
published pointer and marked regions in the four public guides may be changed.
This tool does not commit, push, publish, install or execute downloaded code.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.parse

from verify_release_downloads import (
    DownloadVerificationError, REPOSITORY, REPOSITORY_URL, RAW, SHA,
    validate_tag, verify_release,
)


POINTER = 'release/published.json'
DOCUMENTS = ('README.md', 'README.en.md', 'docs/INSTALL.md', 'docs/en/README.md')
FILES = (POINTER,) + DOCUMENTS
START = '<!-- blackbox:published:start -->'
END = '<!-- blackbox:published:end -->'
MARKERS = re.compile(re.escape(START) + '|' + re.escape(END))
VERSION = re.compile(r'(?<![0-9A-Za-z.])v?([0-9]+\.[0-9]+\.[0-9]+'
                     r'(?:-+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?'
                     r'(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?)')
LINKS = re.compile(r'\]\(<?([^\s)>]+)>?(?:\s+[^)]*)?\)'
                   r'|(?:href|src)\s*=\s*[\"\']([^\"\']+)[\"\']'
                   r'|^\s*\[[^\]\n]+\]:\s*<?([^\s>]+)>?'
                   r'|(https?://[^\s<>\"\x27`]+)', re.IGNORECASE | re.MULTILINE)
SHIELDS = re.compile(r'https://img\.shields\.io/badge/[^\s<>\"\x27`]+')
INSTALL_PATHS = ('release/INSTALL.md', 'release/INSTALL-CODEX.md', 'tools/bootstrap_blackbox.py')
MAX_FILE_BYTES = 2 * 1024 * 1024
POINTER_KEYS = {'schema_version', 'version', 'tag', 'tag_commit', 'archive_sha256'}


class PublishedInstallError(Exception):
    """A safe validation failure without file contents or personal paths."""


def _root(root=None):
    return (Path(root) if root is not None else Path(__file__).resolve().parents[1]).resolve()


def _path(root, name):
    if name not in FILES:
        raise PublishedInstallError('Only the published pointer and public guides may be changed.')
    path = root / name
    if path.is_symlink() or not path.is_file() or path.resolve() != path:
        raise PublishedInstallError('Published installation files must be regular files within the repository.')
    if not path.resolve().is_relative_to(root):
        raise PublishedInstallError('Published installation path escapes the repository.')
    return path


def _read(root):
    result = {}
    for name in FILES:
        path = _path(root, name)
        if path.stat().st_size > MAX_FILE_BYTES:
            raise PublishedInstallError('Published installation file exceeds its size limit.')
        result[name] = path.read_bytes()
    return result


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PublishedInstallError('Published version metadata contains duplicate keys.')
        result[key] = value
    return result


def _pointer(data):
    try:
        value = json.loads(data.decode('utf-8'), object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError) as exc:
        raise PublishedInstallError('Published version metadata is not valid UTF-8 JSON.') from exc
    if not isinstance(value, dict) or set(value) != POINTER_KEYS or type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise PublishedInstallError('Published version metadata has an unsupported schema.')
    try:
        version = validate_tag(value['tag'])
    except DownloadVerificationError as exc:
        raise PublishedInstallError(str(exc)) from exc
    if value['version'] != version:
        raise PublishedInstallError('Published version and tag do not match.')
    if not isinstance(value['tag_commit'], str) or not SHA.fullmatch(value['tag_commit']):
        raise PublishedInstallError('Published version must identify a full lowercase commit SHA.')
    if not isinstance(value['archive_sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', value['archive_sha256']):
        raise PublishedInstallError('Published version must identify a lowercase ZIP SHA256.')
    return value


def _regions(text):
    regions, opened = [], None
    for match in MARKERS.finditer(text):
        if match.group() == START:
            if opened is not None:
                raise PublishedInstallError('Published version markers cannot be nested.')
            opened = match.end()
        else:
            if opened is None:
                raise PublishedInstallError('Published version end marker has no start marker.')
            regions.append((opened, match.start()))
            opened = None
    if opened is not None or not regions:
        raise PublishedInstallError('Every public guide needs complete published version markers.')
    # Catch misspelled markers instead of silently treating them as prose.
    unmarked = MARKERS.sub('', text)
    if 'blackbox:published:' in unmarked:
        raise PublishedInstallError('Published version marker spelling is invalid.')
    return regions


def _inside(position, regions):
    return any(start <= position < end for start, end in regions)


def _validate_document(name, data, pointer):
    try:
        text = data.decode('utf-8')
    except UnicodeError as exc:
        raise PublishedInstallError('Public guides must use UTF-8 text.') from exc
    regions = _regions(text)
    badges = [(match.start(), match.end()) for match in SHIELDS.finditer(text)]
    version, tag = pointer['version'], pointer['tag']
    # Marked versions belong to the pointer. Historical release descriptions
    # remain unmarked and unchanged; active current-version labels and badges
    # must be marked so they cannot silently remain on a previous release.
    for match in VERSION.finditer(text):
        in_badge = _inside(match.start(), badges)
        found = match[1].replace('--', '-') if in_badge else match[1]
        if _inside(match.start(), regions):
            # Shields appends a color to the final version component.
            if found.startswith(version + '-') and in_badge:
                found = found[:len(version)]
            if found != version:
                raise PublishedInstallError('A marked public guide version differs from the published pointer.')
        else:
            line_start = text.rfind('\n', 0, match.start()) + 1
            prefix = text[line_start:match.start()]
            if re.search(r'当前版本|current version|img\.shields\.io/badge/', prefix, re.IGNORECASE):
                raise PublishedInstallError('Current version labels and badges must be inside an update marker.')
    found_codex = False
    allowed_install = {prefix + '/' + tag + '/' + suffix
                       for prefix in (REPOSITORY_URL + '/blob', RAW)
                       for suffix in INSTALL_PATHS}
    for match in LINKS.finditer(text):
        raw_link = next(part for part in match.groups() if part is not None).rstrip('.,;)')
        link = urllib.parse.unquote(raw_link).replace('&amp;', '&')
        parsed = urllib.parse.urlsplit(link)
        lower_path = parsed.path.lower()
        if parsed.netloc == 'img.shields.io' and VERSION.search(parsed.path):
            if ('-' + version.replace('-', '--') + '-' not in parsed.path
                    or not _inside(match.start(), regions)):
                raise PublishedInstallError('Public version badges must use the marked published version and Shields escaping.')
        is_install = any(lower_path.endswith('/' + suffix.lower()) or lower_path == suffix.lower()
                         for suffix in INSTALL_PATHS)
        if not parsed.scheme and not parsed.netloc:
            # A relative INSTALL.md is safe only when it resolves to the stable
            # public guide, never to candidate-bound release/INSTALL.md.
            if lower_path.endswith('install.md'):
                destination = Path(name).parent.joinpath(parsed.path)
                normalized = os.path.normpath(str(destination)).replace('\\', '/')
                if normalized == 'docs/INSTALL.md' and not parsed.query and not parsed.fragment:
                    continue
                is_install = True
        if is_install:
            if link not in allowed_install or not _inside(match.start(), regions):
                raise PublishedInstallError('Public installation links must use the marked, fixed published tag.')
            found_codex = found_codex or parsed.path.endswith('/release/INSTALL-CODEX.md')
        if parsed.netloc == 'github.com' and parsed.path.startswith('/' + REPOSITORY + '/releases/tag/'):
            if link != REPOSITORY_URL + '/releases/tag/' + tag or not _inside(match.start(), regions):
                raise PublishedInstallError('Public release links must match the marked published tag.')
    if not found_codex:
        raise PublishedInstallError('Every public guide must include the fixed published Codex installation link.')
    return text, regions


def _validate(files):
    pointer = _pointer(files[POINTER])
    for name in DOCUMENTS:
        _validate_document(name, files[name], pointer)
    return pointer


def _result(pointer, changed=False):
    return {'published_install': 'passed', 'changed': changed,
            **{key: value for key, value in pointer.items() if key != 'schema_version'}}


def check(root=None):
    """Check public links independently of the candidate/source version."""
    return _result(_validate(_read(_root(root))))


def _precedence(version):
    core, _, suffix = version.partition('+')[0].partition('-')
    return tuple(map(int, core.split('.'))), suffix.split('.') if suffix else None


def _compare_versions(left, right):
    left_core, left_pre = _precedence(left)
    right_core, right_pre = _precedence(right)
    if left_core != right_core:
        return (left_core > right_core) - (left_core < right_core)
    if left_pre is None or right_pre is None:
        return (left_pre is None) - (right_pre is None)
    for one, two in zip(left_pre, right_pre):
        if one == two:
            continue
        if one.isdigit() and two.isdigit():
            return (int(one) > int(two)) - (int(one) < int(two))
        if one.isdigit() != two.isdigit():
            return -1 if one.isdigit() else 1
        return (one > two) - (one < two)
    return (len(left_pre) > len(right_pre)) - (len(left_pre) < len(right_pre))


def _assert_ancestor(root, commit):
    try:
        result = subprocess.run(['git', '-C', str(root), 'merge-base', '--is-ancestor', commit, 'HEAD'],
                                capture_output=True, check=False, timeout=15)
    except subprocess.TimeoutExpired as exc:
        raise PublishedInstallError('Git ancestry validation timed out; no public links were changed.') from exc
    if result.returncode != 0:
        raise PublishedInstallError('The published tag commit must belong to the current HEAD history; fetch complete history first.')


def _render(files, old, new):
    rendered = dict(files)
    for name in DOCUMENTS:
        text, regions = _validate_document(name, files[name], old)
        for start, end in reversed(regions):
            block = text[start:end]
            # Shields escapes hyphens inside its label. Handle its URLs
            # separately, including stable-to-prerelease transitions.
            parts, offset = [], 0
            for badge in SHIELDS.finditer(block):
                parts.append(block[offset:badge.start()].replace(old['version'], new['version']))
                parts.append(badge.group().replace(old['version'].replace('-', '--'), new['version'].replace('-', '--')))
                offset = badge.end()
            parts.append(block[offset:].replace(old['version'], new['version']))
            block = ''.join(parts)
            text = text[:start] + block + text[end:]
        rendered[name] = text.encode('utf-8')
    rendered[POINTER] = (json.dumps(new, indent=2, ensure_ascii=False) + '\n').encode('utf-8')
    _validate(rendered)
    return rendered


def _stage(path, content):
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return Path(temporary)


def _write(root, original, rendered):
    staged, written = {}, []
    try:
        # Prepare and validate everything before the first destination changes.
        for name in FILES:
            if original[name] != rendered[name]:
                staged[name] = _stage(_path(root, name), rendered[name])
        if _read(root) != original:
            raise PublishedInstallError('Public installation files changed during preparation; retry from the current files.')
        for name, temporary in staged.items():
            os.replace(temporary, _path(root, name))
            written.append(name)
    except BaseException:
        restore_failed = False
        for name in reversed(written):
            try:
                temporary = _stage(_path(root, name), original[name])
                try:
                    os.replace(temporary, _path(root, name))
                finally:
                    temporary.unlink(missing_ok=True)
            except OSError:
                restore_failed = True
        if restore_failed:
            raise PublishedInstallError('Public link update failed and rollback was incomplete; restore the working tree before retrying.') from None
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def promote(tag, expected_commit=None, root=None):
    """Verify a public release and atomically replace each allowed local file."""
    root = _root(root)
    files = _read(root)
    old = _validate(files)
    try:
        version = validate_tag(tag)
    except DownloadVerificationError as exc:
        raise PublishedInstallError(str(exc)) from exc
    comparison = _compare_versions(version, old['version'])
    if comparison < 0 or (comparison == 0 and tag != old['tag']):
        raise PublishedInstallError('Published installation versions must increase; downgrades or equal-precedence replacements are forbidden.')
    # Verification is performed here, never trusted from a supplied JSON report.
    verified = verify_release(tag, expected_commit=expected_commit)
    if (verified.get('download_verification') != 'passed' or verified.get('anonymous') is not True
            or verified.get('repository') != REPOSITORY or verified.get('tag') != tag
            or verified.get('version') != version):
        raise PublishedInstallError('Public release verification did not confirm the requested version.')
    new = {'schema_version': 1, 'version': version, 'tag': tag,
           'tag_commit': verified.get('tag_commit'), 'archive_sha256': verified.get('archive_sha256')}
    _pointer(json.dumps(new).encode('utf-8'))
    _assert_ancestor(root, new['tag_commit'])
    if comparison == 0:
        if old != new:
            raise PublishedInstallError('An existing published version cannot change its commit or ZIP checksum.')
        return _result(old)
    rendered = _render(files, old, new)
    _write(root, files, rendered)
    return _result(new, changed=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ('check', 'promote'):
        child = commands.add_parser(command)
        child.add_argument('--root', type=Path, help='Repository root (defaults to this tool repository)')
        if command == 'promote':
            child.add_argument('--tag', required=True)
            child.add_argument('--expected-commit')
    args = parser.parse_args(argv)
    try:
        result = check(args.root) if args.command == 'check' else promote(args.tag, args.expected_commit, args.root)
    except (PublishedInstallError, DownloadVerificationError, OSError, ValueError) as exc:
        message = str(exc) if isinstance(exc, (PublishedInstallError, DownloadVerificationError)) else 'Public installation update failed due to a local I/O error.'
        print(json.dumps({'published_install': 'failed', 'error': message}), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
