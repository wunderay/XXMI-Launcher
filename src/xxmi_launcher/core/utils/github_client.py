import os
import json
import logging
import requests

from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass

from dacite import from_dict
from requests.exceptions import SSLError

from core.locale_manager import L
from core.utils.proxy import ProxyConfig, ProxyManager

log = logging.getLogger(__name__)


@dataclass
class ResponseReleaseAsset:
    name: str
    browser_download_url: str


@dataclass
class ResponseRelease:
    tag_name: str
    body: str
    assets: List[ResponseReleaseAsset]


class GitHubClient:
    API_URL = 'https://api.github.com'

    def __init__(self, cache_path: Path | None = None):
        self.proxy_manager = ProxyManager()
        self.access_token = ''
        self.verify_ssl = False
        # Release responses with their ETags: unchanged releases are answered with an empty
        # `304 Not Modified`, which GitHub does not count against the API rate limit
        self.cache_path = cache_path

    def _cache_file(self, repo_owner, repo_name, pre_release) -> Path | None:
        if self.cache_path is None:
            return None
        return self.cache_path / f'{repo_owner}_{repo_name}_{"releases" if pre_release else "latest"}.json'

    @staticmethod
    def _load_cached(cache_file: Path | None) -> dict | None:
        if cache_file is None or not cache_file.is_file():
            return None
        try:
            cached = json.loads(cache_file.read_text(encoding='utf-8'))
            if isinstance(cached.get('etag'), str) and 'data' in cached:
                return cached
        except Exception:
            log.debug(f'Ignoring unreadable GitHub cache {cache_file}')
        return None

    @staticmethod
    def _save_cached(cache_file: Path | None, etag: str | None, data):
        if cache_file is None or not etag:
            return
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = cache_file.with_name(cache_file.name + '.tmp')
            temporary.write_text(json.dumps({'etag': etag, 'data': data}), encoding='utf-8')
            os.replace(temporary, cache_file)
        except Exception:
            log.debug(f'Failed to write GitHub cache {cache_file}', exc_info=True)

    def configure(self, access_token: Optional[str], verify_ssl: Optional[bool], proxy_config: Optional[ProxyConfig]):
        if access_token is not None:
            self.access_token = access_token.strip()
        if verify_ssl is not None:
            self.verify_ssl = verify_ssl
        if proxy_config is not None:
            self.proxy_manager.configure(proxy_config)

    def fetch_latest_release(self, repo_owner, repo_name,
                             asset_version_pattern, asset_name_format, signature_pattern=None, pre_release=False):
        headers = {}

        if self.access_token:
            headers['Authorization'] = f'token {self.access_token}'

        cache_file = self._cache_file(repo_owner, repo_name, pre_release)
        cached = self._load_cached(cache_file)
        if cached is not None:
            headers['If-None-Match'] = cached['etag']

        try:
            http_response = requests.get(
                url=f'{self.API_URL}/repos/{repo_owner}/{repo_name}/releases{"/latest" if not pre_release else ""}',
                # Only the newest release is used: don't download the whole release list
                params={'per_page': 1} if pre_release else None,
                headers=headers,
                proxies=self.proxy_manager.proxies,
                timeout=10,
                verify=self.verify_ssl
            )
            if http_response.status_code == 304 and cached is not None:
                response = cached['data']
            else:
                response = http_response.json()
                if http_response.status_code == 200 and (isinstance(response, list) or 'tag_name' in response):
                    self._save_cached(cache_file, http_response.headers.get('ETag'), response)
        except SSLError as e:
            raise ValueError(L('error_ssl_certificate_validation_failed', """
                 Failed to validate SSL certificate of GitHub HTTPS connection!
                 
                 If you trust your proxy, uncheck *Verify SSL* in *Launcher Settings* and try again.
            """)) from e
        except Exception as e:
            raise ValueError(L('error_github_connection_failed', """
                 Failed to establish HTTPS connection to GitHub!
                 
                 Please check your Antivirus, Firewall, Proxy and VPN settings.
            """)) from e

        if not isinstance(response, list):
            message, status = response.get('message', None), response.get('status', 0)

            if message is not None:
                message = message.lower()
                status = int(status)
                if 'rate limit' in message:
                    raise ConnectionRefusedError(L('error_github_rate_limit_exceeded', 'GitHub API rate limit exceeded!'))
                elif 'bad credentials' in message or status == 401:
                    raise ConnectionError(L('error_github_invalid_token', """
                         GitHub Personal Access Token is invalid!
                         
                         Please configure correct token in launcher settings.
                    """))

        try:
            if isinstance(response, list):
                response = response[0]
            response = from_dict(data_class=ResponseRelease, data=response)
        except Exception as e:
            raise ValueError(L('error_github_parse_response_failed', 'Failed to parse GitHub response!')) from e

        result = asset_version_pattern.findall(response.tag_name)
        if len(result) != 1:
            raise ValueError(L('error_github_parse_version_failed', 'Failed to parse latest release version!'))
        version = result[0]

        if signature_pattern is None:
            signature = None
        else:
            result = signature_pattern.findall(response.body)
            if len(result) != 1:
                raise ValueError(L('error_github_parse_signature_failed', 'Failed to parse signature!'))
            signature = result[0]

        release_notes = self.parse_release_notes(response.body)

        asset_download_url, manifest_download_url = None, None

        for asset in response.assets:
            if asset.name == asset_name_format % version:
                asset_download_url = asset.browser_download_url
            elif asset.name == 'Manifest.json':
                manifest_download_url = asset.browser_download_url

        if asset_download_url is None:
            raise ValueError(L('error_github_asset_not_found', 
                "Failed to locate asset matching to '{asset_name_format}'!"
            ).format(asset_name_format=asset_name_format))

        return version, asset_download_url, signature, release_notes, manifest_download_url

    def download_data(self, url, block_size=4096, update_progress_callback=None):
        headers = {}

        if self.access_token:
            headers['Authorization'] = f'token {self.access_token}'

        response = requests.get(
            url=url,
            headers=headers,
            proxies=self.proxy_manager.proxies,
            verify=self.verify_ssl,
            timeout=10,
            stream=True
        )

        downloaded_bytes = 0
        total_bytes = int(response.headers.get("content-length", 0))
        if update_progress_callback is not None:
            update_progress_callback(downloaded_bytes, total_bytes)

        data = bytearray()
        for block_data in response.iter_content(block_size):
            data += block_data
            downloaded_bytes += len(block_data)
            if update_progress_callback is not None:
                update_progress_callback(downloaded_bytes, total_bytes)

        return data

    def parse_release_notes(self, body) -> str:
        # Skip warning section header to exclude it from search
        body = body.replace('## Warning', '')
        # Search for start of section
        start = body.find('##')
        if start == -1:
            return L('error_github_invalid_release_notes', '<font color="red">⚠ Error! Invalid release notes format! ⚠</font>')
        # Search for start of signature section (footer)
        end = body.find('## Signature')
        if end == -1:
            return L('error_github_unsigned_release', '<font color="red">☢ Error! Release is unsigned! ☢</font>')
        # Return text inbetween
        return body[start:end]
