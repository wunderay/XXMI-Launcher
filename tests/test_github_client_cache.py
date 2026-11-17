import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.utils import github_client

RELEASE = {
    'tag_name': 'v2.5.0',
    'body': '## Changes\n- Fix\n## Signature\n- SIGNATURE\n',
    'assets': [{'name': 'XXMI-v2.5.0.zip', 'browser_download_url': 'https://example.invalid/a.zip'}],
}
VERSION = re.compile(r'.*?([0-9]+\.[0-9]+\.[0-9]+).*')
SIGNATURE = re.compile(r'^## Signature[\r\n]+- (\S+)\r?$', re.MULTILINE)


class FakeResponse:
    def __init__(self, status, data=None, etag=None):
        self.status_code = status
        self._data = data
        self.headers = {'ETag': etag} if etag else {}

    def json(self):
        if self._data is None:
            raise ValueError('no body')
        return self._data


class GitHubClientCacheTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.cache = Path(temp.name) / 'GitHub'
        self.client = github_client.GitHubClient(cache_path=self.cache)
        self.calls = []

    def fetch(self, responses, pre_release=False):
        def get(**kwargs):
            self.calls.append(kwargs)
            return responses.pop(0)

        with mock.patch.object(github_client.requests, 'get', side_effect=get):
            return self.client.fetch_latest_release('owner', 'repo', VERSION, 'XXMI-v%s.zip', SIGNATURE,
                                                    pre_release=pre_release)

    def test_not_modified_reuses_the_cached_release(self):
        first = self.fetch([FakeResponse(200, RELEASE, '"v1"')])
        self.assertNotIn('If-None-Match', self.calls[0]['headers'])
        second = self.fetch([FakeResponse(304, etag='"v1"')])
        self.assertEqual(self.calls[1]['headers']['If-None-Match'], '"v1"')
        self.assertEqual(first, second)
        self.assertEqual(first[0], '2.5.0')

    def test_pre_release_list_requests_only_the_newest_release(self):
        result = self.fetch([FakeResponse(200, [RELEASE], '"list"')], pre_release=True)
        self.assertEqual(self.calls[0]['params'], {'per_page': 1})
        self.assertTrue(self.calls[0]['url'].endswith('/releases'))
        self.assertEqual(result[0], '2.5.0')
        self.assertTrue((self.cache / 'owner_repo_releases.json').is_file())

    def test_errors_are_not_cached_and_bad_cache_is_ignored(self):
        with self.assertRaises(ConnectionRefusedError):
            self.fetch([FakeResponse(403, {'message': 'API rate limit exceeded', 'status': '403'}, '"err"')])
        self.assertFalse((self.cache / 'owner_repo_latest.json').exists())
        self.cache.mkdir(parents=True, exist_ok=True)
        (self.cache / 'owner_repo_latest.json').write_text('{broken', encoding='utf-8')
        self.assertEqual(self.fetch([FakeResponse(200, RELEASE, '"v2"')])[0], '2.5.0')
        self.assertNotIn('If-None-Match', self.calls[-1]['headers'])
        self.assertEqual(json.loads((self.cache / 'owner_repo_latest.json').read_text())['etag'], '"v2"')

    def test_client_without_cache_path_never_sends_conditional_headers(self):
        self.client = github_client.GitHubClient()
        self.fetch([FakeResponse(200, RELEASE, '"v1"')])
        self.fetch([FakeResponse(200, RELEASE, '"v1"')])
        self.assertNotIn('If-None-Match', self.calls[1]['headers'])


if __name__ == '__main__':
    unittest.main()
