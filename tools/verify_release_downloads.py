"""Verify public Blackbox release downloads without authentication or installation.

Only anonymous HTTPS reads are performed. Package code is never extracted or
executed. Integrity checks do not establish publisher identity or real-host
acceptance. Historical releases may retain their original build provenance.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import http.client
import json
from pathlib import Path
import re
import socket
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from verify_alpha_candidate import VerificationError, read_json, verify_archive


REPOSITORY = 'zemekeznb/blackbox-plugin'
REPOSITORY_URL = 'https://github.com/' + REPOSITORY
API = 'https://api.github.com/repos/' + REPOSITORY
RAW = 'https://raw.githubusercontent.com/' + REPOSITORY
MAX_METADATA_BYTES = 1024 * 1024
MAX_PACKAGE_BYTES = 32 * 1024 * 1024
MAX_CHECKSUM_BYTES = 1024
ATTEMPTS = 3
TIMEOUT = 30
SHA = re.compile(r'[a-f0-9]{40}\Z')
NUMERIC = r'(?:0|[1-9][0-9]*)'
PRERELEASE = r'(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)'
TAG = re.compile(r'v' + NUMERIC + r'\.' + NUMERIC + r'\.' + NUMERIC
                 + r'(?:-' + PRERELEASE + r'(?:\.' + PRERELEASE + r')*)?'
                 + r'(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z')
DOWNLOAD_HOSTS = frozenset({
    'api.github.com', 'github.com', 'raw.githubusercontent.com',
    'release-assets.githubusercontent.com', 'objects.githubusercontent.com',
    'github-releases.githubusercontent.com',
})
TRANSIENT_HTTP = frozenset({408, 429, 500, 502, 503, 504})
# The published .45 bytes predate the public-history cleanup. This exact pair is
# retained as build provenance; it is not a general exemption for moved tags.
HISTORICAL_PROVENANCE = {
    'v0.4.0-alpha.6.45': (
        '9301faf80e1d59c07a8456ff9b72fac5b7719669',
        '3c88cc3fc40bba1cbcd94bbe983108a078a20eaf',
    ),
}


class DownloadVerificationError(Exception):
    """Safe diagnostic that contains no downloaded content or local paths."""


def validate_tag(tag):
    if not isinstance(tag, str) or len(tag) > 128 or not TAG.fullmatch(tag):
        raise DownloadVerificationError('Tag must be a fixed v-prefixed semantic version.')
    return tag[1:]


def _safe_download_url(url):
    try:
        parsed = urllib.parse.urlsplit(url)
        valid = (parsed.scheme == 'https' and parsed.hostname in DOWNLOAD_HOSTS
                 and parsed.port in (None, 443) and not parsed.username
                 and not parsed.password and not parsed.fragment
                 and not re.search(r'[\x00-\x20\x7f]', url))
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise DownloadVerificationError('Download or redirect target is not an allowed public HTTPS host.')


class PublicRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        _safe_download_url(newurl)
        return super().redirect_request(request, response, code, message, headers, newurl)


def _transient_network_error(exc):
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, ssl.SSLError):
        return False
    return (isinstance(reason, (TimeoutError, ConnectionError, http.client.IncompleteRead))
            or isinstance(reason, socket.gaierror) and reason.errno == socket.EAI_AGAIN)


def download(url, limit):
    """Bounded anonymous GET; retry only temporary HTTP/transport failures."""
    _safe_download_url(url)
    opener = urllib.request.build_opener(PublicRedirects())
    for attempt in range(ATTEMPTS):
        request = urllib.request.Request(url, headers={
            'User-Agent': 'Blackbox-Release-Verification/1', 'Accept': '*/*',
        }, method='GET')
        try:
            with opener.open(request, timeout=TIMEOUT) as response:
                _safe_download_url(response.geturl())
                length = response.headers.get('Content-Length')
                if length is not None:
                    try:
                        if int(length) < 0 or int(length) > limit:
                            raise DownloadVerificationError('Public download exceeds the size limit.')
                    except ValueError as exc:
                        raise DownloadVerificationError('Public download has an invalid size header.') from exc
                data = response.read(limit + 1)
            if len(data) > limit:
                raise DownloadVerificationError('Public download exceeds the size limit.')
            return data
        except urllib.error.HTTPError as exc:
            transient = exc.code in TRANSIENT_HTTP
            exc.close()
            if not transient or attempt == ATTEMPTS - 1:
                raise DownloadVerificationError('Public download failed with HTTP status ' + str(exc.code) + '.') from exc
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            if not _transient_network_error(exc) or attempt == ATTEMPTS - 1:
                raise DownloadVerificationError('Public download failed due to a network or TLS error.') from exc
        time.sleep(attempt + 1)
    raise DownloadVerificationError('Public download did not complete.')


def _json(data):
    try:
        value = read_json(data)
    except VerificationError as exc:
        raise DownloadVerificationError('Public release metadata is invalid JSON.') from exc
    if not isinstance(value, dict):
        raise DownloadVerificationError('Public release metadata must be a JSON object.')
    return value


def _tag_commit(tag, fetch):
    reference = _json(fetch(API + '/git/ref/tags/' + tag, MAX_METADATA_BYTES))
    if reference.get('ref') != 'refs/tags/' + tag:
        raise DownloadVerificationError('Public Git reference does not match the requested tag.')
    target = reference.get('object')
    seen = set()
    for _ in range(6):
        if (not isinstance(target, dict) or not isinstance(target.get('sha'), str)
                or not SHA.fullmatch(target['sha'])):
            raise DownloadVerificationError('Public tag has an invalid Git object identity.')
        sha = target['sha']
        if target.get('type') == 'commit':
            return sha
        if target.get('type') != 'tag' or sha in seen:
            raise DownloadVerificationError('Public tag does not resolve to a commit.')
        seen.add(sha)
        annotated = _json(fetch(API + '/git/tags/' + sha, MAX_METADATA_BYTES))
        if annotated.get('sha') != sha:
            raise DownloadVerificationError('Annotated tag object identity does not match.')
        target = annotated.get('object')
    raise DownloadVerificationError('Public tag exceeds the annotated tag depth limit.')


def _literal(tree, name):
    values = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == name for target in node.targets):
            values.append(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            values.append(node.value)
    if len(values) != 1 or not isinstance(values[0], ast.Constant) or not isinstance(values[0].value, str):
        raise DownloadVerificationError('Bootstrap identity must use one literal version and repository.')
    return values[0].value


def _check_entrypoints(bootstrap, instructions, tag):
    try:
        tree = ast.parse(bootstrap.decode('utf-8-sig'))
        text = instructions.decode('utf-8-sig')
    except (SyntaxError, UnicodeError, ValueError, RecursionError) as exc:
        raise DownloadVerificationError('Public installation entrypoints are not valid text or Python.') from exc
    if _literal(tree, 'VERSION') != tag[1:] or _literal(tree, 'REPOSITORY') != REPOSITORY_URL:
        raise DownloadVerificationError('Public bootstrap version or repository does not match.')
    urls = re.findall(r'https://[^\s`<>]+/tools/bootstrap_blackbox\.py\b', text)
    allowed = {RAW + '/' + tag + '/tools/bootstrap_blackbox.py',
               REPOSITORY_URL + '/blob/' + tag + '/tools/bootstrap_blackbox.py'}
    if not urls or not set(urls).issubset(allowed):
        raise DownloadVerificationError('Installation instructions must point to this fixed tag bootstrap.')


def verify_release(tag, expected_commit=None, fetch=None):
    """Verify bytes from a public release; fetch(url, limit) is injectable in tests."""
    version = validate_tag(tag)
    if expected_commit is not None and (not isinstance(expected_commit, str) or not SHA.fullmatch(expected_commit)):
        raise DownloadVerificationError('Expected commit must be a full lowercase GitHub commit SHA.')
    fetch_impl = fetch or download

    def fetch(url, limit):
        data = fetch_impl(url, limit)
        if not isinstance(data, bytes) or len(data) > limit:
            raise DownloadVerificationError('Public download exceeds its limit or has invalid content.')
        return data

    release = _json(fetch(API + '/releases/tags/' + tag, MAX_METADATA_BYTES))
    if release.get('draft') is not False or release.get('tag_name') != tag:
        raise DownloadVerificationError('Release must be published and match the requested tag.')
    if release.get('html_url') != REPOSITORY_URL + '/releases/tag/' + tag:
        raise DownloadVerificationError('Release page is not the expected public repository URL.')
    stem = 'blackbox-plugin-' + version + '-candidate'
    zip_name, checksum_name, manifest_name = stem + '.zip', stem + '.zip.sha256', stem + '.release-manifest.json'
    limits = {zip_name: MAX_PACKAGE_BYTES, checksum_name: MAX_CHECKSUM_BYTES, manifest_name: MAX_METADATA_BYTES}
    assets = release.get('assets')
    if not isinstance(assets, list) or len(assets) != 3:
        raise DownloadVerificationError('Release must contain exactly the ZIP, checksum and manifest assets.')
    by_name = {}
    for asset in assets:
        if not isinstance(asset, dict) or not isinstance(asset.get('name'), str):
            raise DownloadVerificationError('Release asset metadata is invalid.')
        name = asset['name']
        if name not in limits or name in by_name:
            raise DownloadVerificationError('Release assets have unexpected or duplicate names.')
        expected_url = REPOSITORY_URL + '/releases/download/' + tag + '/' + name
        if asset.get('browser_download_url') != expected_url or asset.get('state') != 'uploaded':
            raise DownloadVerificationError('Release asset URL or upload state is invalid.')
        if type(asset.get('size')) is not int or not 0 < asset['size'] <= limits[name]:
            raise DownloadVerificationError('Release asset size is invalid or exceeds its limit.')
        by_name[name] = asset

    tag_commit = _tag_commit(tag, fetch)
    if expected_commit is not None and tag_commit != expected_commit:
        raise DownloadVerificationError('Public tag does not match the expected source commit.')
    downloaded = {}
    for name in (checksum_name, manifest_name, zip_name):
        asset = by_name[name]
        data = fetch(asset['browser_download_url'], limits[name])
        if len(data) != asset['size']:
            raise DownloadVerificationError('Downloaded asset size differs from public release metadata.')
        downloaded[name] = data
    checksum = re.fullmatch(rb'([a-f0-9]{64})  ' + re.escape(zip_name.encode('ascii')) + rb'(?:\r?\n)?',
                            downloaded[checksum_name])
    digest = hashlib.sha256(downloaded[zip_name]).hexdigest()
    if not checksum or checksum[1].decode('ascii') != digest:
        raise DownloadVerificationError('ZIP checksum or exact checksum filename does not match.')
    try:
        with tempfile.TemporaryDirectory(prefix='blackbox-release-verify-') as temporary:
            archive = Path(temporary) / 'candidate.zip'
            archive.write_bytes(downloaded[zip_name])
            manifest, payload = verify_archive(archive)
    except VerificationError as exc:
        raise DownloadVerificationError('Release archive integrity verification failed.') from exc
    if manifest['version'] != version:
        raise DownloadVerificationError('Archive version does not match the requested release.')
    if payload['release-manifest.json'] != downloaded[manifest_name]:
        raise DownloadVerificationError('External manifest bytes differ from the embedded release manifest.')
    if sum(map(len, payload.values())) > MAX_PACKAGE_BYTES:
        raise DownloadVerificationError('Release payload exceeds the current installer size limit.')
    plugin = _json(payload.get('plugins/blackbox/.codex-plugin/plugin.json', b''))
    if plugin.get('version') != version or plugin.get('name') != 'blackbox':
        raise DownloadVerificationError('Packaged plugin identity or version does not match.')
    source = manifest['source']
    source_commit = source.get('commit')
    if not isinstance(source_commit, str) or not SHA.fullmatch(source_commit) or source.get('dirty') is not False:
        raise DownloadVerificationError('Release manifest must identify a clean source commit.')
    if expected_commit is not None and source_commit != expected_commit:
        raise DownloadVerificationError('Release manifest source does not match the expected source commit.')
    if (source_commit != tag_commit
            and HISTORICAL_PROVENANCE.get(tag) != (tag_commit, source_commit)):
        raise DownloadVerificationError('Release manifest source differs from the public tag commit.')
    entrypoints = {}
    for path in ('release/INSTALL-CODEX.md', 'tools/bootstrap_blackbox.py'):
        data = fetch(RAW + '/' + tag + '/' + path, MAX_METADATA_BYTES)
        if payload.get(path) != data:
            raise DownloadVerificationError('Public installation entrypoint bytes differ from the packaged payload.')
        entrypoints[path] = data
    _check_entrypoints(entrypoints['tools/bootstrap_blackbox.py'], entrypoints['release/INSTALL-CODEX.md'], tag)
    return {
        'download_verification': 'passed', 'repository': REPOSITORY, 'tag': tag,
        'version': version, 'anonymous': True, 'tag_commit': tag_commit,
        'manifest_source_commit': source_commit, 'source_matches_tag': source_commit == tag_commit,
        'source_check': ('expected_commit_verified' if expected_commit is not None else
                         'matches_tag' if source_commit == tag_commit else
                         'historical_provenance_differs_from_current_tag'),
        'archive_sha256': digest, 'asset_count': 3, 'verified_file_count': len(payload),
        'installation_entrypoints': 'byte_identical_to_payload',
        'package_code_executed': False, 'plugin_installed': False,
        'publisher_authenticity': 'not_independently_verified', 'real_host_acceptance': 'not_tested',
    }


def main(argv=None):
    parser = argparse.ArgumentParser(prog='verify_release_downloads.py', description=__doc__)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--expected-commit', help='Require both tag and payload provenance to equal this full commit SHA')
    parser.add_argument('--output', type=Path, help='Write the public verification result to a new JSON file')
    args = parser.parse_args(argv)
    try:
        result = verify_release(args.tag, args.expected_commit)
        encoded = json.dumps(result, indent=2, ensure_ascii=False) + '\n'
        if args.output:
            with args.output.open('x', encoding='utf-8', newline='\n') as output:
                output.write(encoded)
    except (DownloadVerificationError, OSError, ValueError) as exc:
        message = str(exc) if isinstance(exc, DownloadVerificationError) else 'Release verification failed due to a local I/O error.'
        print(json.dumps({'download_verification': 'failed', 'error': message}), file=sys.stderr)
        return 2
    print(encoded, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
