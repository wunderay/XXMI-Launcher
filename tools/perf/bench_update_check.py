#!/usr/bin/env python3
"""Bytes and rate-limited requests used by update checks, against a local mock of the GitHub
releases API (30 releases with realistic notes; honours per_page and If-None-Match/ETag).

Runs the real core.utils.github_client for the stable (/releases/latest) and pre-release
(/releases) channels, three checks each, as three hourly checks would.
Usage: PYTHONPATH=src/xxmi_launcher python3 tools/perf/bench_update_check.py
"""
import hashlib
import json
import re
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

SIGNATURE = 'A' * 128


def make_release(index: int) -> dict:
    version = f'2.{30 - index}.0'
    notes = '\n'.join(f'- Change {index}.{line}: improved something in the launcher behaviour' for line in range(40))
    return {
        'tag_name': f'v{version}',
        'body': f'## Changes\n{notes}\n## Signature\n- {SIGNATURE}\n',
        'assets': [{'name': f'XXMI-Launcher-Installer-Online-v{version}.msi',
                    'browser_download_url': f'https://example.invalid/{version}.msi'},
                   {'name': 'Manifest.json', 'browser_download_url': f'https://example.invalid/{version}.json'}],
        'author': {'login': 'owner', 'id': 1, 'avatar_url': 'https://example.invalid/a.png', 'type': 'User'},
        'html_url': f'https://example.invalid/releases/v{version}', 'id': 1000 + index, 'draft': False,
        'prerelease': index == 0, 'created_at': '2026-11-01T00:00:00Z', 'published_at': '2026-11-01T00:00:00Z',
    }


RELEASES = [make_release(i) for i in range(30)]


class Handler(BaseHTTPRequestHandler):
    stats = {'requests': 0, 'counted_requests': 0, 'body_bytes': 0}

    def do_GET(self):
        url = urlparse(self.path)
        per_page = int(parse_qs(url.query).get('per_page', ['30'])[0])
        data = RELEASES[0] if url.path.endswith('/latest') else RELEASES[:per_page]
        body = json.dumps(data).encode()
        etag = '"' + hashlib.sha1(body).hexdigest() + '"'
        Handler.stats['requests'] += 1
        if self.headers.get('If-None-Match') == etag:
            self.send_response(304)
            self.send_header('ETag', etag)
            self.end_headers()
            return
        Handler.stats['counted_requests'] += 1  # GitHub counts every non-304 response
        Handler.stats['body_bytes'] += len(body)
        self.send_response(200)
        self.send_header('ETag', etag)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    local = f'http://127.0.0.1:{server.server_port}'
    import requests
    from core.utils import github_client
    original_get = requests.get

    def local_get(url, **kwargs):
        kwargs.pop('proxies', None)
        return original_get(url.replace('https://api.github.com', local), **kwargs)

    results = {}
    with tempfile.TemporaryDirectory() as cache, mock.patch.object(requests, 'get', local_get):
        try:
            client = github_client.GitHubClient(cache_path=Path(cache))
        except TypeError:  # client without a cache
            client = github_client.GitHubClient()
        for channel, pre_release in (('stable', False), ('pre-release', True)):
            Handler.stats.update(requests=0, counted_requests=0, body_bytes=0)
            versions = [client.fetch_latest_release('owner', 'repo', re.compile(r'.*?([0-9]+\.[0-9]+\.[0-9]+).*'),
                                                    'XXMI-Launcher-Installer-Online-v%s.msi',
                                                    re.compile(r'^## Signature[\r\n]+- (\S+)\r?$', re.MULTILINE),
                                                    pre_release=pre_release)[0] for _ in range(3)]
            results[channel] = {**Handler.stats, 'versions': sorted(set(versions))}
    server.shutdown()
    print(json.dumps(results))


if __name__ == '__main__':
    sys.exit(main())
