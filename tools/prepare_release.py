"""Validate a clean candidate and create a new GitHub Release draft only.

Never publish, reuse an existing tag/release, replace assets, or attest real-host
acceptance. A failed remote mutation may leave a new tag or partial draft; inspect
it manually rather than rerunning or deleting it automatically.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from build_alpha import (BuildError, PLUGIN, _linked,
                         check_install_entrypoints, checked_file, collect_payload,
                         source_identity)
from verify_alpha_candidate import VerificationError, read_json, verify_archive
from verify_release_downloads import (DownloadVerificationError, MAX_CHECKSUM_BYTES,
                                      MAX_METADATA_BYTES, MAX_PACKAGE_BYTES, SHA,
                                      _check_entrypoints, validate_tag)


REPOSITORY = 'zemekeznb/blackbox-plugin'
ROOT = Path(__file__).resolve().parents[1]


class ReleaseError(Exception):
    """A content-free release validation or operation failure."""


def run_gh(arguments):
    """Use argv, fixed GitHub host, and captured output; never a shell."""
    try:
        return subprocess.run(
            ['gh', *arguments], capture_output=True, encoding='utf-8',
            errors='strict', timeout=180, check=False,
            env=dict(os.environ, GH_HOST='github.com', GH_PROMPT_DISABLED='1',
                     GH_PAGER='cat', NO_COLOR='1'))
    except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
        raise ReleaseError('GitHub CLI did not complete; remote outcome may be unknown.') from exc


def api(endpoint, *, method='GET', fields=(), allow_missing=False, expected_status=None):
    arguments = ['api', '--hostname', 'github.com', '--include', '--method', method,
                 '-H', 'Accept: application/vnd.github+json',
                 '-H', 'X-GitHub-Api-Version: 2022-11-28', endpoint]
    for key, value in fields:
        arguments.extend(['--raw-field', f'{key}={value}'])
    result = run_gh(arguments)
    headers, separator, body = result.stdout.replace('\r\n', '\n').partition('\n\n')
    status = re.match(r'HTTP/\S+ ([0-9]{3})(?: |$)', headers)
    if not separator or not status:
        raise ReleaseError('GitHub returned no verifiable HTTP status; stopping.')
    code = int(status[1])
    if allow_missing and method == 'GET' and code == 404:
        return None
    expected = expected_status if expected_status is not None else (201 if method == 'POST' else 200)
    if result.returncode or code != expected:
        raise ReleaseError(f'GitHub request failed (HTTP {code}); stopping.')
    try:
        return read_json(body.encode('utf-8'))
    except VerificationError as exc:
        raise ReleaseError('GitHub returned invalid JSON; stopping.') from exc


def validate_source(tag, commit, root=ROOT):
    try:
        validate_tag(tag)
    except DownloadVerificationError as exc:
        raise ReleaseError(str(exc)) from exc
    if not isinstance(commit, str) or not SHA.fullmatch(commit):
        raise ReleaseError('Commit must be a complete lowercase Git object ID.')
    root = Path(root).resolve()
    identity = source_identity(root)
    if (identity.get('commit') != commit or identity.get('dirty') is not False
            or identity.get('status') != 'available'):
        raise ReleaseError('Checkout must be the requested exact commit with a known clean working tree.')
    try:
        entrypoints = {name: checked_file(root, name).read_bytes() for name in (
            PLUGIN + '.codex-plugin/plugin.json', 'tools/bootstrap_blackbox.py',
            'release/INSTALL-CODEX.md', 'LICENSE')}
        metadata = read_json(entrypoints[PLUGIN + '.codex-plugin/plugin.json'])
        if not isinstance(metadata, dict) or metadata.get('version') != tag[1:]:
            raise ReleaseError('Tag must match plugin.json version; existing versions must not be reused.')
        check_install_entrypoints(entrypoints, tag[1:])
        for name in ('tools/bootstrap_blackbox.py', 'release/INSTALL-CODEX.md'):
            if len(entrypoints[name]) > MAX_METADATA_BYTES:
                raise ReleaseError('Installation entrypoint exceeds the public download size limit.')
        _check_entrypoints(entrypoints['tools/bootstrap_blackbox.py'],
                           entrypoints['release/INSTALL-CODEX.md'], tag)
        if not entrypoints['LICENSE'].strip():
            raise ReleaseError('An approved, nonempty LICENSE is required.')
    except (BuildError, VerificationError, DownloadVerificationError) as exc:
        raise ReleaseError(str(exc)) from exc
    return {'tag': tag, 'version': tag[1:], 'commit': commit,
            'source': identity, 'published': False}


def releases():
    """Read every page so an old draft cannot be mistaken for a free tag."""
    page = 1
    while True:
        items = api(f'repos/{REPOSITORY}/releases?per_page=100&page={page}')
        if not isinstance(items, list) or any(
                not isinstance(item, dict) or not isinstance(item.get('tag_name'), str)
                for item in items):
            raise ReleaseError('GitHub returned an invalid release list; stopping.')
        yield from items
        if len(items) < 100:
            break
        page += 1


def ensure_available(tag, commit):
    repository = api(f'repos/{REPOSITORY}')
    if not isinstance(repository, dict) or repository.get('full_name') != REPOSITORY:
        raise ReleaseError('GitHub repository identity could not be verified; stopping.')
    if api(f'repos/{REPOSITORY}/git/ref/tags/{tag}', allow_missing=True) is not None:
        raise ReleaseError('Remote tag already exists; it will not be moved or reused.')
    # Installation tokens (including GITHUB_TOKEN) need not expose the user's
    # permissions.push field. Verify the credential's actual Contents(write)
    # capability instead: generate-notes requires that permission but does not
    # save its output or create a tag/release. Discard the generated text.
    # https://docs.github.com/en/rest/releases/releases#generate-release-notes-content-for-a-release
    try:
        notes = api(f'repos/{REPOSITORY}/releases/generate-notes', method='POST',
                    fields=(('tag_name', tag), ('target_commitish', commit)), expected_status=200)
    except ReleaseError as exc:
        raise ReleaseError('Full preflight needs verified Contents write access to see drafts. '
                           'Use check --local-only with a read-only CI token. ' + str(exc)) from exc
    if (not isinstance(notes, dict) or not isinstance(notes.get('name'), str)
            or not isinstance(notes.get('body'), str)):
        raise ReleaseError('GitHub did not confirm the non-persisting release permission check; stopping.')
    if any(item['tag_name'] == tag for item in releases()):
        raise ReleaseError('A draft or published Release already uses this tag; it will not be changed.')


def check(tag, commit, *, root=ROOT, local_only=False):
    result = validate_source(tag, commit, root)
    if not local_only:
        ensure_available(tag, commit)
    return dict(result, remote_availability='not_checked' if local_only else 'available',
                checks_only=True)


def validate_artifacts(directory, tag, commit, root=ROOT):
    directory = Path(directory).absolute()
    if not directory.is_dir() or any(_linked(part) for part in (directory, *directory.parents)):
        raise ReleaseError('Artifacts must be a regular directory without links or reparse points.')
    stem = f'blackbox-plugin-{tag[1:]}-candidate'
    names = [stem + '.zip', stem + '.zip.sha256', stem + '.release-manifest.json']
    limits = dict(zip(names, (MAX_PACKAGE_BYTES, MAX_CHECKSUM_BYTES, MAX_METADATA_BYTES)))
    if {path.name for path in directory.iterdir()} != set(names):
        raise ReleaseError('Artifacts must contain exactly the expected ZIP, checksum, and manifest.')
    try:
        paths = {name: checked_file(directory.resolve(), name) for name in names}
        if any(not 0 < path.stat().st_size <= limits[name] for name, path in paths.items()):
            raise ReleaseError('Release artifact exceeds the verification size limit.')
        artifact_bytes = {name: path.read_bytes() for name, path in paths.items()}
        digest = hashlib.sha256(artifact_bytes[names[0]]).hexdigest()
        expected_checksum = f'{digest}  {names[0]}\n'.encode('ascii')
        if artifact_bytes[names[1]] != expected_checksum:
            raise ReleaseError('Outer ZIP checksum or checksum filename does not match.')
        manifest, files = verify_archive(io.BytesIO(artifact_bytes[names[0]]))
        if sum(map(len, files.values())) > MAX_PACKAGE_BYTES:
            raise ReleaseError('Verified payload exceeds the installer size limit.')
        if artifact_bytes[names[2]] != files['release-manifest.json']:
            raise ReleaseError('External and archived manifests must be byte-identical.')
        if (manifest.get('version') != tag[1:] or manifest.get('product') != 'Blackbox Plugin'
                or manifest.get('artifact_kind') != 'alpha_candidate'
                or manifest.get('source') != {'commit': commit, 'dirty': False, 'status': 'available'}):
            raise ReleaseError('Candidate must identify the requested version and exact clean source commit.')
        if ('LICENSE' not in files or not files['LICENSE'].strip()
                or manifest.get('license_pending') is not False):
            raise ReleaseError('Candidate must include its approved LICENSE without a pending-license flag.')
        blockers = manifest.get('release_blockers')
        if (manifest.get('publish_ready') is not False or not isinstance(blockers, list)
                or any(not isinstance(item, str) for item in blockers)
                or not {'real_host_acceptance_not_attested', 'publication_not_authorized_by_builder'}.issubset(blockers)):
            raise ReleaseError('Candidate must retain publication and real-host acceptance boundaries.')
        # A self-consistent manifest alone cannot prove that the ZIP came from
        # this checkout. Compare its payload with the builder's current allowlist.
        payload = collect_payload(Path(root).resolve())
        if {name: data for name, data in files.items()
                if name not in ('release-manifest.json', 'SHA256SUMS')} != payload:
            raise ReleaseError('Candidate payload differs from the checked-out source allowlist.')
        check_install_entrypoints(payload, tag[1:])
        _check_entrypoints(payload['tools/bootstrap_blackbox.py'],
                           payload['release/INSTALL-CODEX.md'], tag)
    except (BuildError, VerificationError, DownloadVerificationError) as exc:
        raise ReleaseError(str(exc)) from exc
    return artifact_bytes


def create_draft(tag, commit, artifacts, *, root=ROOT):
    validate_source(tag, commit, root)
    contents = validate_artifacts(artifacts, tag, commit, root)
    # Snapshot validated bytes, so gh never uploads files changed after validation.
    with tempfile.TemporaryDirectory(prefix='blackbox-release-') as temporary:
        directory = Path(temporary)
        paths = []
        for name, data in contents.items():
            path = directory / name
            path.write_bytes(data)
            paths.append(path)
        notes = directory / 'notes.md'
        notes.write_text(
            f'Blackbox Plugin {tag[1:]} — release draft\n\n'
            f'Candidate source commit: `{commit}`.\n\n'
            'Automated checks validate the source, candidate and isolated reader only. '
            'Real Codex desktop installation, page access and actual Hook delivery '
            'still require maintainer acceptance before publishing.\n\n'
            'Review and complete these notes before manually publishing. '
            'This draft does not change the README installation entry or installed plugins.\n',
            encoding='utf-8')
        # Repeat local evidence immediately before the remote checks and mutation.
        validate_source(tag, commit, root)
        ensure_available(tag, commit)
        try:
            ref = api(f'repos/{REPOSITORY}/git/refs', method='POST', fields=(
                ('ref', 'refs/tags/' + tag), ('sha', commit)))
            if (not isinstance(ref, dict) or ref.get('ref') != 'refs/tags/' + tag
                    or not isinstance(ref.get('object'), dict)
                    or ref.get('object', {}).get('sha') != commit
                    or ref.get('object', {}).get('type') != 'commit'):
                raise ReleaseError('New tag identity could not be verified.')
        except ReleaseError as exc:
            raise ReleaseError('New tag creation failed or is uncertain. Inspect GitHub before retrying; '
                               'no tag was updated or automatically deleted.') from exc
        arguments = ['release', 'create', tag, *map(str, paths),
                     '--repo', 'github.com/' + REPOSITORY, '--verify-tag', '--target', commit,
                     '--draft', '--latest=false', '--title', 'Blackbox Plugin ' + tag[1:],
                     '--notes-file', str(notes)]
        prerelease = '-' in tag.split('+', 1)[0]
        if prerelease:
            arguments.append('--prerelease')
        try:
            response = run_gh(arguments)
            if response.returncode:
                raise ReleaseError('GitHub CLI could not confirm draft creation.')
            matches = [item for item in releases() if item['tag_name'] == tag]
            if len(matches) != 1:
                raise ReleaseError('Could not identify exactly one new draft.')
            release = matches[0]
            assets = release.get('assets')
            if (release.get('draft') is not True or release.get('published_at') is not None
                    or release.get('prerelease') is not prerelease
                    or release.get('target_commitish') != commit
                    or not isinstance(assets, list) or len(assets) != 3
                    or any(not isinstance(item, dict) or not isinstance(item.get('name'), str) for item in assets)
                    or {item['name'] for item in assets} != set(contents)):
                raise ReleaseError('Draft metadata or attachments did not match the request.')
            for asset in assets:
                data = contents[asset['name']]
                if (asset.get('state') != 'uploaded' or asset.get('size') != len(data)
                        or asset.get('digest') not in (None, 'sha256:' + hashlib.sha256(data).hexdigest())):
                    raise ReleaseError('Uploaded attachment metadata did not match the candidate.')
            url = release.get('html_url')
            if not isinstance(url, str) or not url.startswith(f'https://github.com/{REPOSITORY}/releases/'):
                raise ReleaseError('Draft URL could not be verified.')
        except ReleaseError as exc:
            raise ReleaseError('The new tag exists; draft creation or verification did not complete. '
                               'Inspect the tag and any partial draft manually; nothing was published '
                               'or automatically removed by this script.') from exc
    return {'state': 'draft', 'tag': tag, 'commit': commit, 'url': url,
            'asset_count': 3, 'published': False, 'real_host_acceptance': 'not_tested'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('check', 'create-draft'):
        command = commands.add_parser(name)
        command.add_argument('--tag', required=True)
        command.add_argument('--commit', required=True)
        if name == 'check':
            command.add_argument('--local-only', action='store_true',
                                 help='Validate source without gh/network; does not check tag or draft availability')
        else:
            command.add_argument('--artifacts', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'check':
            result = check(args.tag, args.commit, local_only=args.local_only)
        else:
            result = create_draft(args.tag, args.commit, args.artifacts)
    except (ReleaseError, OSError) as exc:
        message = str(exc) if isinstance(exc, ReleaseError) else 'Local file operation failed.'
        print(json.dumps({'status': 'failed', 'error': message, 'published': False}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
