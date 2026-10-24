"""Experimental ModLoading contract. Capability metadata never changes DLL trust."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


CAPABILITY_FILE = 'PreparationCapabilities.json'
MODES = {'off': 'Off', 'adaptive': 'Adaptive', 'prepared_profile': 'Prepared profile'}
LIMITS = {
    'cpu_cache_mb': (0, 4096), 'gpu_cache_mb': (0, 4096),
    'max_asset_mb': (1, 1024), 'worker_threads': (1, 16),
    'max_pending_jobs': (1, 4096), 'create_budget_ms': (1, 50),
    'upload_budget_mb': (1, 1024),
}


@dataclass
class PreparedLoadingSettings:
    preparation_mode: str = 'off'
    preparation_cpu_cache_mb: int = 512
    preparation_gpu_cache_mb: int = 256
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
        return (f'Compatibility: {self.reason}\n'
                'Preparation status: Unknown / offline (runtime telemetry unavailable).\n'
                'Preparing and Ready require live native telemetry. This is separate from mod enabled status.\n'
                f'Deployed DLL SHA256: {self.dll_sha256 or "unavailable"}')


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
        return
    mode = settings.preparation_mode
    if mode not in capability.modes:
        mode = 'off'
    if mode == 'off':
        ini.set_option('ModLoading', 'mode', 'off')
        ini.set_option('ModLoading', 'auto_activate', 0)
        ini.set_option('ModLoading', 'preparation_loading_window', 0)
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
    values.update(mode=mode, auto_activate=0,
                  preparation_loading_window=int(settings.preparation_loading_window is True))
    for key, value in values.items():
        ini.set_option('ModLoading', key, value)
