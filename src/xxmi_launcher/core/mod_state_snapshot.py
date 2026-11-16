#!/usr/bin/env python3
"""Complete, offline XXMI-root snapshots; compatible with standalone v1 archives.

Native UI operations run this engine only in the isolated mod_state_worker,
after the initiating launcher has exited. No GUI/configuration modules imported.
"""

from __future__ import annotations

import argparse
import csv
import fnmatch
import hashlib
import io
import json
import ntpath
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
import uuid
import zipfile

FORMAT = "xxmi-root-snapshot"
VERSION = 1
MANIFEST = "SNAPSHOT-MANIFEST.json"
CONFIG = "XXMI Launcher Config.json"
CHUNK = 1024 * 1024
MAX_MANIFEST = 64 * 1024 * 1024
RESERVED = {"CON", "PRN", "AUX", "NUL", "CLOCK$"} | {
    f"{prefix}{digit}" for prefix in ("COM", "LPT") for digit in "123456789¹²³"
}
PROCESS_NAMES = {
    "zenlesszonezero.exe", "xxmi launcher.exe", "xxmi launcher no console.exe",
    "xxmi launcher updater.exe",
    "genshinimpact.exe", "yuanshen.exe", "starrail.exe", "client-win64-shipping.exe",
    "bh3.exe", "honkaiimpact3.exe", "endfield.exe",
}
# Set by the isolated worker only after runtime isolation and parent exit.
# Guard exemption is additionally tied to os.getpid(), never an arbitrary PID.
_WORKER_PID = None


class SnapshotError(Exception):
    """An actionable refusal or failed verification."""


def fail(message: str):
    raise SnapshotError(message)


def _is_link(st: os.stat_result) -> bool:
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & 0x400)


def checked_path(path: Path | str) -> Path:
    """Absolute path without resolving away symlinks/junctions."""
    path = Path(os.path.abspath(os.fspath(path)))
    for part in reversed([path, *path.parents]):
        try:
            st = part.lstat()
        except FileNotFoundError:
            continue
        if _is_link(st):
            fail(f"Symlinks, junctions and reparse points are unsupported: {part}")
    return path


def inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def safe_relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        fail(f"Unsafe relative path: {value!r}")
    if value.startswith("/") or ntpath.splitdrive(value)[0]:
        fail(f"Absolute or drive-qualified archive path: {value!r}")
    parts = value.split("/")
    for part in parts:
        if part in ("", ".", "..") or part.endswith((" ", ".")):
            fail(f"Unsafe path component: {value!r}")
        if any(ord(char) < 32 or char in '<>:"|?*' for char in part):
            fail(f"Windows-incompatible path: {value!r}")
        if part.split(".")[0].rstrip(" .").upper() in RESERVED:
            fail(f"Windows reserved filename: {value!r}")
    return value


def _fingerprint(st: os.stat_result) -> tuple:
    return (st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino, st.st_dev)


def inventory(root: Path) -> dict[str, dict]:
    root = checked_path(root)
    if not root.is_dir():
        fail(f"Launcher root is not a directory: {root}")
    result = {}
    folded = {}
    def walk_error(exc):
        fail(f"Cannot enumerate every source directory: {exc}")

    for folder, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
        for name in sorted(dirs + files):
            path = Path(folder) / name
            relative = safe_relative(path.relative_to(root).as_posix())
            key = relative.casefold()
            if key in folded:
                fail(f"Case-insensitive path collision: {folded[key]} / {relative}")
            folded[key] = relative
            st = path.lstat()
            if _is_link(st):
                fail(f"Symlinks, junctions and reparse points are unsupported: {path}")
            if not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)):
                fail(f"Unsupported special file: {path}")
            result[relative] = {"kind": "dir" if stat.S_ISDIR(st.st_mode) else "file",
                                "fingerprint": _fingerprint(st), "mtime_ns": st.st_mtime_ns,
                                "size": st.st_size}
    return result


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        fail(f"Cannot read required launcher config {path}: {exc}")
    if not isinstance(value, dict):
        fail(f"Expected a JSON object: {path}")
    return value


def guard_processes(config: dict | None = None, root: Path | None = None,
                    reference_root: Path | None = None, *, ignore_current_launcher=False):
    """Windows-only guard; never starts, stops or kills the game/launcher."""
    if os.name != "nt":
        return
    names = set(PROCESS_NAMES)

    def collect(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {"process_exe_names", "game_process_exe", "game_exe_names"}:
                    entries = value if isinstance(value, list) else [value]
                    for entry in entries:
                        if isinstance(entry, str):
                            names.update(ntpath.basename(x.strip()).casefold() for x in re.split(r"[,;\n]", entry) if x.strip())
                else:
                    collect(value)
        elif isinstance(node, list):
            for child in node:
                collect(child)
    collect(config or {})
    # A configured Loader target can differ from the standard game executable.
    if root is not None:
        for item in (config or {}).get("Importers", {}).values():
            if not isinstance(item, dict) or not isinstance(item.get("Importer"), dict):
                continue
            folder = item["Importer"].get("importer_folder")
            if not isinstance(folder, str):
                continue
            normalized = folder.replace("\\", os.sep)
            path = checked_path(Path(normalized) if os.path.isabs(normalized) else root / normalized)
            if reference_root is not None and inside(path, reference_root):
                path = root / path.relative_to(reference_root)
            ini = path / "d3dx.ini"
            if not inside(path, root) or not ini.is_file():
                continue
            section = ""
            for line in _ini_text(ini).splitlines():
                line = line.strip()
                if line.startswith("["):
                    section = line.split("]", 1)[0][1:].strip().casefold()
                elif section == "loader" and "=" in line and not line.startswith((";", "#")):
                    key, value = line.split("=", 1)
                    if key.strip().casefold() == "target":
                        names.add(ntpath.basename(value.split(";", 1)[0].strip().strip('"')).casefold())
    try:
        completed = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True,
                                   text=True, errors="replace", timeout=30, check=True)
        rows = list(csv.reader(io.StringIO(completed.stdout)))
        if not rows or any(len(row) < 2 or not row[1].isdigit() for row in rows):
            fail("Cannot interpret Windows process query; close game/launcher and retry.")
    except (OSError, subprocess.SubprocessError, csv.Error) as exc:
        fail(f"Windows process query failed; refusing to proceed: {exc}")
    running = sorted({row[0] for row in rows if row[0].casefold() in names and
                      not ((_WORKER_PID == os.getpid() or ignore_current_launcher) and
                           int(row[1]) == os.getpid())})
    if running:
        fail("Close these processes before proceeding: " + ", ".join(running))


def _ini_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeError:
            continue
    fail(f"Cannot decode INI file for dependency inspection: {path}")


def _directives(path: Path):
    section = ""
    for number, line in enumerate(_ini_text(path).splitlines(), 1):
        line = line.strip()
        if line.startswith("["):
            section = line.split("]", 1)[0][1:].strip().casefold()
            continue
        if not line or line.startswith((";", "#")) or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().casefold()
        shader_path = section.startswith("customshader") and key in {"vs", "ps", "gs", "hs", "ds", "cs"}
        if key not in {"include", "include_recursive", "exclude_recursive", "user_config", "filename"} and not shader_path:
            continue
        if key in {"user_config", "exclude_recursive"} and section != "include":
            continue
        if key in {"include", "include_recursive"} and not section.startswith("include"):
            continue
        value = value.split(";", 1)[0].strip()
        if value.startswith(('"', "'")) and value[-1:] == value[0]:
            value = value[1:-1]
        if not value:
            continue
        if shader_path and value.casefold() in {"null", "none"}:
            continue
        yield key, value, number


def dependency_path(value: str, base: Path, root: Path, origin: str) -> Path:
    if any(token in value for token in ("%", "$", "*", "?", "\x00")):
        fail(f"Unsupported dynamic/wildcard dependency at {origin}: {value!r}")
    # ntpath recognizes Windows absolute/UNC paths even during Linux validation.
    if ntpath.splitdrive(value)[0] or value.startswith("\\"):
        if os.name != "nt" or not ntpath.isabs(value):
            fail(f"Unsupported/external Windows dependency at {origin}: {value!r}")
    value = value.replace("\\", os.sep)
    path = checked_path(Path(value) if os.path.isabs(value) else base / value)
    if not inside(path, root):
        fail(f"External dependency cannot be captured at {origin}: {value!r}")
    return path


def inspect_root(root: Path | str, *, reference_root: Path | None = None,
                 check_processes: bool = True) -> dict:
    root = checked_path(root)
    entries = inventory(root)
    config = _read_json(root / CONFIG)
    if check_processes:
        guard_processes(config, root, reference_root)

    def resolve(value, base, origin):
        # Absolute paths captured under the original root still point there after
        # restore. During staging, validate their equivalent staged destinations.
        candidate = value.replace("\\", os.sep)
        if reference_root is not None and os.path.isabs(candidate):
            absolute = Path(os.path.abspath(candidate))
            if inside(absolute, reference_root):
                value = str(root / absolute.relative_to(reference_root))
        return dependency_path(value, base, root, origin)
    try:
        importer = config["Importers"]["ZZMI"]["Importer"]
        folder = importer.get("importer_folder", "ZZMI/")
        if not isinstance(folder, str) or not folder.strip():
            fail("ZZMI importer_folder must be a nonempty path string.")
    except (KeyError, TypeError, AttributeError):
        fail(f"Required Importers.ZZMI.Importer settings are missing in {CONFIG}.")
    zzmi = resolve(folder, root, "ZZMI importer_folder")
    if not zzmi.is_dir() or not (zzmi / "d3dx.ini").is_file() or not (zzmi / "Mods").is_dir():
        fail(f"Expected ZZMI importer with d3dx.ini and Mods directory: {zzmi}")
    warnings = []
    if importer.get("extra_libraries_enabled"):
        libraries = importer.get("extra_libraries", "")
        if not isinstance(libraries, str):
            fail("Unsupported ZZMI extra_libraries format.")
        for library in libraries.splitlines():
            if library.strip():
                path = resolve(library.strip(), root, "ZZMI extra_libraries")
                if not path.is_file():
                    fail(f"Configured extra library does not exist: {path}")
    for key, value in importer.items():
        if value and key.endswith("enabled") and any(word in key for word in ("pre_launch", "post_launch", "post_load", "custom_launch")):
            warnings.append(f"Enabled custom command {key}: external command effects are not captured or executed.")
    # Inspect active ZZMI dependencies. Unrelated old INIs are backed up as bytes.
    main_ini = zzmi / "d3dx.ini"
    parsed = {main_ini: list(_directives(main_ini))}
    visited = set()
    states = {resolve(value, zzmi, f"{main_ini}:{line}")
              for key, value, line in parsed[main_ini] if key == "user_config"}
    exclusions = [value for key, value, _ in parsed[main_ini] if key == "exclude_recursive"]
    for pattern in exclusions:
        if any(char in pattern for char in ("/", "\\", "[", "]", "\x00")):
            fail(f"Unsupported exclude_recursive glob {pattern!r}; only basename patterns with * and ? are supported.")

    def visit(path):
        if path in visited:
            return
        visited.add(path)
        if path not in parsed:
            if not path.is_file():
                fail(f"Included INI is missing: {path}")
            parsed[path] = list(_directives(path))
        for key, value, line in parsed[path]:
            if key in {"user_config", "exclude_recursive"}:
                # Native user_config is selected from the root INI before includes.
                continue
            target = resolve(value, path.parent, f"{path}:{line} ({key})")
            if key not in {"include", "include_recursive"}:
                # Native resource/shader file resolution falls back to the DLL
                # directory if the containing INI directory candidate is absent.
                if not target.exists():
                    resolve(value, zzmi, f"{path}:{line} ({key}, importer fallback)")
                continue
            if key == "include":
                visit(target)
            else:
                if not target.is_dir():
                    fail(f"Recursive include directory is missing: {target}")
                for relative, entry in sorted(entries.items()):
                    candidate = root / relative
                    if entry["kind"] == "file" and relative.lower().endswith(".ini") and inside(candidate, target):
                        if any(fnmatch.fnmatchcase(part.casefold(), pattern.casefold())
                               for part in candidate.relative_to(target).parts for pattern in exclusions):
                            continue
                        visit(candidate)
    visit(main_ini)
    if len(states) > 1:
        fail("Ambiguous user_config paths in main [Include]: " + ", ".join(map(str, sorted(states))))
    state = next(iter(states), zzmi / "d3dx_user.ini")
    if not state.is_file() or state.stat().st_size == 0:
        fail(f"Saved settings file is missing or empty: {state}. In the game save mod settings with F10, then exit the game and launcher before backing up.")
    return {"root": str(root), "zzmi": str(zzmi), "user_config": str(state),
            "files": sum(e["kind"] == "file" for e in entries.values()),
            "bytes": sum(e["size"] for e in entries.values() if e["kind"] == "file"),
            "scope": "Entire launcher root, including every other importer present", "warnings": warnings}


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _zip_info(name: str, directory=False) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name + ("/" if directory else ""))
    info.create_system = 3
    info.external_attr = ((stat.S_IFDIR | 0o755) if directory else (stat.S_IFREG | 0o644)) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def backup(root: Path | str, archive: Path | str) -> dict:
    root, archive = checked_path(root), checked_path(archive)
    if inside(archive, root):
        fail("The backup archive must be outside the launcher root.")
    if archive.exists():
        fail(f"Archive already exists; refusing to overwrite: {archive}")
    if not archive.parent.is_dir():
        fail(f"Archive parent directory does not exist: {archive.parent}")
    before = inventory(root)
    root_stat = _fingerprint(root.stat())
    inspection = inspect_root(root)
    manifest = {"format": FORMAT, "version": VERSION, "created_utc": datetime.now(timezone.utc).isoformat(),
                "original_root": str(root), "root_mtime_ns": root.stat().st_mtime_ns,
                "inspection": inspection, "directories": [], "files": []}
    fd, temporary = tempfile.mkstemp(prefix=".xxmi-backup-", suffix=".partial", dir=archive.parent)
    os.close(fd)
    temporary = Path(temporary)
    try:
        with zipfile.ZipFile(temporary, "w", allowZip64=True) as target:
            for relative, entry in sorted(before.items()):
                path = root / relative
                if entry["kind"] == "dir":
                    target.writestr(_zip_info("payload/" + relative, True), b"")
                    manifest["directories"].append({"path": relative, "mtime_ns": entry["mtime_ns"]})
                    continue
                digest = hashlib.sha256()
                if _fingerprint(path.lstat()) != entry["fingerprint"]:
                    fail(f"Source changed during backup: {path}")
                with path.open("rb") as source, target.open(_zip_info("payload/" + relative), "w", force_zip64=True) as dest:
                    if _fingerprint(os.fstat(source.fileno())) != entry["fingerprint"]:
                        fail(f"Source changed during backup: {path}")
                    while chunk := source.read(CHUNK):
                        digest.update(chunk)
                        dest.write(chunk)
                    if _fingerprint(os.fstat(source.fileno())) != entry["fingerprint"]:
                        fail(f"Source changed during backup: {path}")
                if _fingerprint(path.lstat()) != entry["fingerprint"]:
                    fail(f"Source changed during backup: {path}")
                manifest["files"].append({"path": relative, "size": entry["size"], "mtime_ns": entry["mtime_ns"], "sha256": digest.hexdigest()})
            target.writestr(_zip_info(MANIFEST), _json_bytes(manifest))
        if inventory(root) != before or _fingerprint(root.stat()) != root_stat:
            fail("Source files/directories changed, appeared or disappeared during backup; retry after closing game and launcher.")
        guard_processes(_read_json(root / CONFIG), root)
        verify(temporary)
        # Hard-link publication is atomic and fails if the requested filename exists.
        # Same-directory temporary file guarantees the same filesystem.
        with temporary.open("r+b") as completed:
            os.fsync(completed.fileno())
        if os.name == "nt":
            # Windows rename refuses an existing destination (also works on exFAT).
            temporary.rename(archive)
        else:
            os.link(temporary, archive)
            temporary.unlink()
        return {"archive": str(archive), "files": len(manifest["files"]), "bytes": inspection["bytes"], "warnings": inspection["warnings"]}
    finally:
        if temporary.exists():
            temporary.unlink()


def _manifest(archive: zipfile.ZipFile) -> tuple[dict, dict[str, zipfile.ZipInfo]]:
    members = {}
    folded = {}
    for info in archive.infolist():
        name = info.filename[:-1] if info.is_dir() else info.filename
        if info.orig_filename != info.filename:
            fail("Archive member contains a NUL or ambiguous filename.")
        safe_relative(name)
        if info.flag_bits & 1:
            fail(f"Encrypted archive member is unsupported: {name}")
        mode = info.external_attr >> 16
        if info.create_system == 0 and info.external_attr & 0x400:
            fail(f"Windows reparse archive member: {name}")
        if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
            fail(f"Symlink or special archive member: {name}")
        if info.is_dir() != stat.S_ISDIR(mode) and stat.S_IFMT(mode) != 0:
            fail(f"Archive member type disagreement: {name}")
        if name in members or name.casefold() in folded:
            fail(f"Duplicate/case-colliding archive member: {name}")
        members[name] = info
        folded[name.casefold()] = name
    if MANIFEST not in members or members[MANIFEST].is_dir():
        fail("Required snapshot manifest is missing.")
    if members[MANIFEST].file_size > MAX_MANIFEST:
        fail("Snapshot manifest exceeds the supported 64 MiB limit.")
    try:
        manifest = json.loads(archive.read(MANIFEST))
    except (ValueError, UnicodeError) as exc:
        fail(f"Invalid snapshot manifest: {exc}")
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT or manifest.get("version") != VERSION:
        fail("Unsupported snapshot format/version.")
    if not isinstance(manifest.get("original_root"), str) or not os.path.isabs(manifest["original_root"]):
        fail("Manifest original_root must be an absolute path on this platform.")
    if not isinstance(manifest.get("root_mtime_ns"), int):
        fail("Missing root timestamp.")
    expected = {MANIFEST}
    entries = {}
    for collection, kind in (("directories", "dir"), ("files", "file")):
        if not isinstance(manifest.get(collection), list):
            fail(f"Missing manifest {collection} list.")
        for item in manifest[collection]:
            if not isinstance(item, dict):
                fail("Invalid manifest entry.")
            relative = safe_relative(item.get("path"))
            if relative.casefold() in entries:
                fail(f"Manifest duplicate/case collision: {relative}")
            entries[relative.casefold()] = (relative, kind)
            if not isinstance(item.get("mtime_ns"), int):
                fail(f"Invalid timestamp: {relative}")
            name = "payload/" + relative
            expected.add(name)
            if name not in members or members[name].is_dir() != (kind == "dir"):
                fail(f"Missing or mismatched archive entry: {relative}")
            if kind == "file" and (not isinstance(item.get("size"), int) or item["size"] < 0 or
                                   members[name].file_size != item["size"] or
                                   not isinstance(item.get("sha256"), str) or
                                   not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])):
                fail(f"Invalid file metadata: {relative}")
    for relative, kind in entries.values():
        for parent in PurePosixPath(relative).parents:
            if str(parent) == ".":
                continue
            if entries.get(str(parent).casefold()) != (str(parent), "dir"):
                fail(f"Missing/mismatched parent directory: {relative}")
    if set(members) != expected:
        fail("Archive inventory does not exactly match its manifest.")
    if not any(item["path"] == CONFIG for item in manifest["files"]):
        fail("Snapshot is missing the launcher configuration.")
    return manifest, members


def _verify_files(archive: zipfile.ZipFile, manifest: dict, stage: Path | None = None):
    for entry in manifest["files"]:
        digest = hashlib.sha256()
        total = 0
        output = (stage / entry["path"]).open("xb") if stage else None
        try:
            with archive.open("payload/" + entry["path"]) as source:
                while chunk := source.read(CHUNK):
                    total += len(chunk)
                    if total > entry["size"]:
                        fail(f"Archive exceeds declared file size: {entry['path']}")
                    digest.update(chunk)
                    if output:
                        output.write(chunk)
            if total != entry["size"] or digest.hexdigest() != entry["sha256"]:
                fail(f"Hash/size mismatch: {entry['path']}")
        finally:
            if output:
                output.close()
        if stage:
            os.utime(stage / entry["path"], ns=(entry["mtime_ns"], entry["mtime_ns"]))


def verify(archive: Path | str, root: Path | str | None = None) -> dict:
    archive = checked_path(archive)
    with zipfile.ZipFile(archive) as source:
        manifest, _ = _manifest(source)
        _verify_files(source, manifest)
    result = {"archive": str(archive), "verified": True, "files": len(manifest["files"]), "original_root": manifest["original_root"]}
    if root is not None:
        root = checked_path(root)
        current = inventory(root)
        expected = {item["path"]: "dir" for item in manifest["directories"]}
        expected.update({item["path"]: "file" for item in manifest["files"]})
        differences = sorted(path for path in current.keys() | expected.keys()
                             if path not in current or path not in expected or current[path]["kind"] != expected[path])
        for entry in manifest["files"]:
            path = root / entry["path"]
            if entry["path"] in differences:
                continue
            digest = hashlib.sha256()
            with path.open("rb") as source:
                while chunk := source.read(CHUNK):
                    digest.update(chunk)
            if digest.hexdigest() != entry["sha256"] or path.stat().st_mtime_ns != entry["mtime_ns"]:
                differences.append(entry["path"])
        for entry in manifest["directories"]:
            if entry["path"] not in differences and (root / entry["path"]).stat().st_mtime_ns != entry["mtime_ns"]:
                differences.append(entry["path"])
        result["root_matches"] = not differences
        result["differences"] = sorted(set(differences))
    return result


def _write_journal(path: Path, data: dict, first=False):
    with path.open("x" if first else "w", encoding="utf-8") as output:
        output.write(json.dumps(data, indent=2) + "\n")
        output.flush()
        os.fsync(output.fileno())


def restore(archive: Path | str, root: Path | str, apply=False) -> dict:
    archive, root = checked_path(archive), checked_path(root)
    if inside(archive, root):
        fail("Restore archive must be outside the target launcher root.")
    with zipfile.ZipFile(archive) as source:
        manifest, _ = _manifest(source)
        original = checked_path(manifest["original_root"])
        if os.path.normcase(str(root)) != os.path.normcase(str(original)):
            fail(f"Restore is restricted to the original root: {original}. Relocation may break absolute paths.")
        _verify_files(source, manifest)
        config = json.loads(source.read("payload/" + CONFIG).decode("utf-8-sig"))
        guard_processes(config)
        if root.exists():
            inventory(root)
            if (root / CONFIG).exists():
                guard_processes(_read_json(root / CONFIG), root)
        if not root.parent.is_dir():
            fail(f"Restore parent directory does not exist: {root.parent}")
        result = {"archive": str(archive), "root": str(root), "files": len(manifest["files"]), "apply": bool(apply),
                  "plan": "Replace entire root; retain existing root in a sibling rollback folder."}
        if not apply:
            return result
        stage = Path(tempfile.mkdtemp(prefix=f".{root.name}.restore-stage-", dir=root.parent))
        try:
            for entry in sorted(manifest["directories"], key=lambda x: len(PurePosixPath(x["path"]).parts)):
                (stage / entry["path"]).mkdir()
            _verify_files(source, manifest, stage)
            for entry in sorted(manifest["directories"], key=lambda x: len(PurePosixPath(x["path"]).parts), reverse=True):
                os.utime(stage / entry["path"], ns=(entry["mtime_ns"], entry["mtime_ns"]))
            os.utime(stage, ns=(manifest["root_mtime_ns"], manifest["root_mtime_ns"]))
            staged = inventory(stage)
            if set(staged) != {x["path"] for x in manifest["files"] + manifest["directories"]}:
                fail("Staging inventory does not match the snapshot.")
            for entry in manifest["files"]:
                path = stage / entry["path"]
                digest = hashlib.sha256()
                with path.open("rb") as staged_file:
                    while chunk := staged_file.read(CHUNK):
                        digest.update(chunk)
                if path.stat().st_size != entry["size"] or digest.hexdigest() != entry["sha256"]:
                    fail(f"Staged file failed on-disk verification: {entry['path']}")
            # Read staged config/INIs too: restoration must meet backup safety prerequisites.
            inspection = inspect_root(stage, reference_root=original)
            guard_processes(config, stage, original)
            checked_path(root)
            if root.exists():
                inventory(root)
                if (root / CONFIG).exists():
                    guard_processes(_read_json(root / CONFIG), root)
        except BaseException:
            shutil.rmtree(stage)
            raise
    token = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    rollback = root.with_name(root.name + ".rollback-" + token)
    journal = root.with_name(root.name + ".restore-" + token + ".json")
    record = {"status": "staged", "root": str(root), "archive": str(archive), "staging": str(stage),
              "rollback": str(rollback), "had_previous_root": root.exists()}
    try:
        _write_journal(journal, record, first=True)
    except OSError as exc:
        shutil.rmtree(stage)
        fail(f"Cannot write restore recovery journal; live root was untouched: {exc}")
    moved = False
    try:
        if root.exists():
            root.rename(rollback)
            moved = True
            record["status"] = "previous-root-preserved"
            _write_journal(journal, record)
        stage.rename(root)
    except BaseException as exc:
        recovery = ""
        if moved and not root.exists():
            try:
                rollback.rename(root)
                recovery = " Previous root was restored."
                record["status"] = "failed-previous-root-restored"
            except OSError as rollback_exc:
                recovery = f" Previous root remains at {rollback}; recovery rename failed: {rollback_exc}."
                record["status"] = "failed-manual-recovery-required"
        else:
            record["status"] = "failed-live-root-unchanged"
        record["error"] = str(exc)
        try:
            _write_journal(journal, record)
        except OSError:
            pass
        fail(f"Restore rename failed: {exc}.{recovery} Staging: {stage}. Journal: {journal}")
    record["status"] = "complete"
    try:
        _write_journal(journal, record)
    except OSError as exc:
        result["journal_warning"] = str(exc)
    result.update({"rollback": str(rollback) if moved else None, "journal": str(journal), "warnings": inspection["warnings"]})
    return result


def default_root() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        fail("APPDATA is not set. Supply --root explicitly.")
    return Path(appdata) / "XXMI Launcher"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect_parser = commands.add_parser("inspect", help="Check closed-game persisted settings and snapshot scope")
    inspect_parser.add_argument("--root", type=Path)
    backup_parser = commands.add_parser("backup", help="Create a complete verified launcher-root snapshot")
    backup_parser.add_argument("--root", type=Path)
    backup_parser.add_argument("--archive", type=Path)
    verify_parser = commands.add_parser("verify", help="Verify all archive hashes; optionally compare an existing root")
    verify_parser.add_argument("--archive", type=Path, required=True)
    verify_parser.add_argument("--root", type=Path)
    restore_parser = commands.add_parser("restore", help="Plan a restore; --apply is required to change files")
    restore_parser.add_argument("--archive", type=Path, required=True)
    restore_parser.add_argument("--root", type=Path, required=True)
    restore_parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            result = inspect_root(args.root or default_root())
        elif args.command == "backup":
            archive = args.archive
            if archive is None:
                destination = Path(__file__).resolve().parent / "Backups"
                destination.mkdir(exist_ok=True)
                archive = destination / ("XXMI-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8] + ".zip")
            result = backup(args.root or default_root(), archive)
        elif args.command == "verify":
            result = verify(args.archive, args.root)
        else:
            result = restore(args.archive, args.root, args.apply)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 2 if result.get("root_matches") is False else 0
    except (SnapshotError, OSError, ValueError, zipfile.BadZipFile, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
