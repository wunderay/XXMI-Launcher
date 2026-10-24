"""Experimental ModLoading contract. Capability metadata never changes DLL trust."""

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


CAPABILITY_FILE = 'PreparationCapabilities.json'
STATUS_FILE = Path('.xxmi-preparation') / 'status.json'
MODES = {'off': 'Off', 'adaptive': 'Adaptive', 'prepared_profile': 'Prepared profile'}
REFRESH_KEYS = {'': 'Disabled', 'ctrl shift no_alt VK_F8': 'Ctrl+Shift+F8',
                'ctrl shift no_alt VK_F9': 'Ctrl+Shift+F9',
                'ctrl shift no_alt VK_F11': 'Ctrl+Shift+F11'}
LIMITS = {
    'cpu_cache_mb': (0, 4096), 'gpu_cache_mb': (0, 4096),
    'disk_cache_mb': (0, 4096),
    'max_asset_mb': (1, 1024), 'worker_threads': (1, 16),
    'max_pending_jobs': (1, 4096), 'create_budget_ms': (1, 50),
    'upload_budget_mb': (1, 1024),
}


@dataclass
class PreparedLoadingSettings:
    preparation_mode: str = 'off'
    preparation_cpu_cache_mb: int = 512
    preparation_gpu_cache_mb: int = 256
    preparation_disk_cache_mb: int = 0
    preparation_refresh_key: str = ''
    preparation_max_asset_mb: int = 64
    preparation_worker_threads: int = 2
    preparation_max_pending_jobs: int = 64
    preparation_create_budget_ms: int = 2
    preparation_upload_budget_mb: int = 16
    preparation_loading_window: bool = False


@dataclass(frozen=True)
class PreparationCapability:
    supported: bool
    reason: str
    dll_sha256: str = ''
    modes: tuple[str, ...] = ('off',)

    @property
    def diagnostics(self) -> str:
        return self.format_diagnostics()

    def format_diagnostics(self, status: 'PreparationStatus | None' = None) -> str:
        status = status or PreparationStatus(False, reason='Runtime report unavailable.')
        return (f'Compatibility: {self.reason}\n'
                f'Preparation status: {status.label}\n{status.diagnostics}\n'
                'Preparation is separate from mod enabled status.\n'
                f'Deployed DLL SHA256: {self.dll_sha256 or "unavailable"}')


@dataclass(frozen=True)
class PreparationStatus:
    available: bool
    state: str = 'unknown'
    reason: str = ''
    process_id: int = 0
    mode: str = 'off'
    total_assets: int = 0
    cpu_ready: int = 0
    gpu_ready: int = 0
    compatibility_assets: int = 0
    process_verified: bool = False
    updated_unix_ms: int = 0

    @property
    def label(self) -> str:
        return f'{self.state.capitalize()} (reported)' if self.available else 'Unknown / offline'

    @property
    def diagnostics(self) -> str:
        if not self.available:
            return self.reason
        liveness = 'live process' if self.process_verified else 'liveness unverified'
        return (f'Source: reported PID {self.process_id} ({liveness}); diagnostic report only, '
                'not authentication or authorization.\n'
                f'Mode: {self.mode}; assets: {self.total_assets}; CPU ready: {self.cpu_ready}; '
                f'GPU ready: {self.gpu_ready}; compatibility: {self.compatibility_assets}.\n'
                f'Report timestamp (Unix ms): {self.updated_unix_ms}; freshness checked when read.\n'
                f'Reason: {self.reason or "none reported"}')


def read_preparation_status(path: Path, capability: PreparationCapability, *,
                            now_ms: int | None = None,
                            process_is_alive: Callable[[int], bool] | None = None) -> PreparationStatus:
    """Read bounded, fresh diagnostic telemetry only after package authentication.

    The report itself is not signed. Matching a signed DLL capability and a live
    reported PID does not authenticate its writer, so no action uses this data.
    """
    if not capability.supported:
        return PreparationStatus(False, reason='An authenticated compatible package is required for runtime reports.')
    try:
        with path.open('rb') as report:
            data = report.read(8193)
        if len(data) > 8192:
            raise ValueError('runtime report exceeds 8 KiB')
        value = json.loads(data)
        if type(value.get('schema_version')) is not int or value['schema_version'] != 1:
            raise ValueError('unsupported runtime report schema')
        if value.get('dll_sha256') != capability.dll_sha256 or not capability.dll_sha256:
            raise ValueError('runtime report DLL identity does not match the authenticated capability')
        integer_keys = ('process_id', 'updated_unix_ms', 'total_assets', 'cpu_ready', 'gpu_ready', 'compatibility_assets')
        if any(type(value.get(key)) is not int or not 0 <= value[key] <= 2**63 - 1 for key in integer_keys):
            raise ValueError('invalid runtime report integer fields')
        if value['process_id'] == 0 or value['process_id'] > 2**32 - 1:
            raise ValueError('invalid runtime report process ID')
        age = (int(time.time() * 1000) if now_ms is None else now_ms) - value['updated_unix_ms']
        if age > 3000 or age < -1000:
            raise ValueError('runtime report is stale or its timestamp is in the future')
        mode, state = value.get('mode'), value.get('state')
        if mode not in capability.modes or state not in ('off', 'preparing', 'ready', 'compatibility', 'failed'):
            raise ValueError('invalid runtime report mode or state')
        if (mode == 'off') != (state == 'off'):
            raise ValueError('runtime report mode and state disagree')
        total, cpu, gpu, compatibility = (value[key] for key in ('total_assets', 'cpu_ready', 'gpu_ready', 'compatibility_assets'))
        if cpu > total or gpu > cpu or compatibility > total:
            raise ValueError('runtime report asset counts are inconsistent')
        if state == 'ready' and (mode != 'prepared_profile' or total == 0 or cpu != total or gpu != total or compatibility != 0):
            raise ValueError('runtime report Ready requires a prepared profile, all assets CPU/GPU ready and zero compatibility assets')
        reason = value.get('reason')
        if not isinstance(reason, str) or len(reason.encode('utf-8')) > 256 or any(ord(char) < 32 for char in reason):
            raise ValueError('invalid runtime report reason')
        if process_is_alive is not None:
            try:
                alive = process_is_alive(value['process_id'])
            except Exception:
                return PreparationStatus(False, reason='Runtime report process liveness could not be verified.')
            if not alive:
                raise ValueError('runtime report process is no longer running')
        return PreparationStatus(True, state, reason, value['process_id'], mode, total, cpu, gpu,
                                 compatibility, process_is_alive is not None, value['updated_unix_ms'])
    except (OSError, ValueError, TypeError, AttributeError, KeyError, RecursionError) as error:
        return PreparationStatus(False, reason=f'Runtime report unavailable: {error}')


def inspect_capability(dll_path: Path, marker_path: Path,
                       authenticate: Callable[[Path, bytes], bool]) -> PreparationCapability:
    """Only signed, versioned metadata bound to the deployed DLL may enable options.

    The caller uses the existing package public key and manifest signature. An
    arbitrary sidecar, DLL version resource, or unsafe-mode flag is insufficient.
    """
    try:
        if marker_path.stat().st_size > 65536:
            raise ValueError('capability metadata exceeds 64 KiB')
        marker_bytes = marker_path.read_bytes()
        if len(marker_bytes) > 65536 or not authenticate(marker_path, marker_bytes):
            raise ValueError('capability metadata is not authenticated')
        marker = json.loads(marker_bytes)
        if (type(marker.get('schema_version')) is not int or marker['schema_version'] != 1
                or type(marker.get('mod_loading_version')) is not int or marker['mod_loading_version'] != 1):
            raise ValueError('unsupported preparation capability version')
        digest = hashlib.sha256()
        with dll_path.open('rb') as dll:
            for block in iter(lambda: dll.read(1024 * 1024), b''):
                digest.update(block)
        dll_hash = digest.hexdigest()
        if marker.get('dll_sha256') != dll_hash:
            return PreparationCapability(False, 'Capability does not match deployed DLL.', dll_hash)
        modes = marker.get('modes')
        if not isinstance(modes, list) or not modes or any(mode not in MODES for mode in modes) or 'off' not in modes:
            raise ValueError('invalid supported preparation modes')
        return PreparationCapability(True, 'Authenticated preparation protocol v1.', dll_hash, tuple(modes))
    except (OSError, ValueError, TypeError, AttributeError, KeyError) as error:
        return PreparationCapability(False, f'Preparation unavailable: {error}')


def validate_limit(key: str, value: int) -> str:
    lower, upper = LIMITS[key]
    if type(value) is not int or not lower <= value <= upper:
        return f'Enter an integer from {lower} to {upper}.'
    if key in ('cpu_cache_mb', 'gpu_cache_mb') and 0 < value < 16:
        return 'Use 0 to disable the cache, or an integer from 16 to 4096.'
    return ''


def apply_preparation_settings(ini, settings: PreparedLoadingSettings,
                               capability: PreparationCapability) -> None:
    """Apply through the launcher's existing atomic INI save; never touch mods.

    Unsupported DLLs get no new section. A leftover experimental section is
    explicitly disabled when capability authentication fails after an update.
    """
    if not capability.supported:
        if ini.get_section('ModLoading') is not None:
            ini.set_option('ModLoading', 'mode', 'off')
            ini.set_option('ModLoading', 'auto_activate', 0)
            ini.set_option('ModLoading', 'preparation_loading_window', 0)
            ini.set_option('ModLoading', 'refresh_key', '')
            ini.set_option('ModLoading', 'disk_cache_mb', 0)
        return
    mode = settings.preparation_mode
    if mode not in capability.modes:
        mode = 'off'
    if mode == 'off':
        ini.set_option('ModLoading', 'mode', 'off')
        ini.set_option('ModLoading', 'auto_activate', 0)
        ini.set_option('ModLoading', 'preparation_loading_window', 0)
        ini.set_option('ModLoading', 'refresh_key', '')
        ini.set_option('ModLoading', 'disk_cache_mb', 0)
        return
    values = {}
    for key in LIMITS:
        value = getattr(settings, f'preparation_{key}')
        error = validate_limit(key, value)
        if error:
            raise ValueError(f'ModLoading {key}: {error}')
        values[key] = value
    if values['cpu_cache_mb'] and values['max_asset_mb'] > values['cpu_cache_mb']:
        raise ValueError('ModLoading max_asset_mb must not exceed the CPU cache limit.')
    if settings.preparation_refresh_key not in REFRESH_KEYS:
        raise ValueError('ModLoading refresh_key: choose a supported refresh shortcut.')
    values.update(mode=mode, auto_activate=0,
                  refresh_key=settings.preparation_refresh_key,
                  preparation_loading_window=int(settings.preparation_loading_window is True))
    for key, value in values.items():
        ini.set_option('ModLoading', key, value)
