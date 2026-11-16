import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

from core import mod_state_snapshot as snapshot


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.root = self.parent / "XXMI Launcher"
        self.zzmi = self.root / "ZZMI"
        self.mods = self.zzmi / "Mods"
        (self.mods / "DISABLED Old Skin").mkdir(parents=True)
        (self.mods / "Renamed Skin").mkdir()
        (self.mods / "Empty Folder").mkdir()
        self.write(self.root / snapshot.CONFIG, json.dumps({"Importers": {"ZZMI": {"Importer": {"importer_folder": "ZZMI/"}}}}))
        self.write(self.zzmi / "d3dx.ini", "[Include]\ninclude_recursive = Mods\nuser_config = d3dx_user.ini\n")
        self.state = self.zzmi / "d3dx_user.ini"
        self.state_bytes = b"[Constants]\r\n$\\Mods\\Renamed Skin\\mod.ini\\dress = 3\r\n"
        self.write(self.state, self.state_bytes)
        self.write(self.mods / "Renamed Skin" / "mod.ini", "[Constants]\n$persisted = 3\n")
        self.write(self.mods / "DISABLED Old Skin" / "old.ini", "; disabled\n")
        self.write(self.zzmi / "d3d11.dll", b"\0dll\xff")
        self.write(self.root / "GIMI" / "Mods" / "Other game.bin", b"other importer data")
        self.archive = self.parent / "snapshot.zip"

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value.encode("utf-8") if isinstance(value, str) else value)

    def make_archive(self):
        snapshot.backup(self.root, self.archive)

    def mutate_archive(self, transform):
        replacement = self.parent / "modified.zip"
        with zipfile.ZipFile(self.archive) as source, zipfile.ZipFile(replacement, "w") as dest:
            for info in source.infolist():
                result = transform(info.filename, source.read(info))
                if result is not None:
                    name, data = result
                    new_info = snapshot._zip_info(name[:-1] if name.endswith("/") else name, name.endswith("/"))
                    dest.writestr(new_info, data)
        self.archive.unlink()
        replacement.rename(self.archive)

    def test_roundtrip_settings_names_all_files_and_mtimes(self):
        original = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.make_archive()
        self.state.write_bytes(b"wrong settings")
        (self.mods / "Renamed Skin").rename(self.mods / "different-name")
        self.write(self.mods / "stale.ini", "stale mod")
        dry = snapshot.restore(self.archive, self.root)
        self.assertFalse(dry["apply"])
        self.assertEqual(self.state.read_bytes(), b"wrong settings")
        result = snapshot.restore(self.archive, self.root, apply=True)
        self.assertEqual(self.state.read_bytes(), self.state_bytes)
        self.assertFalse((self.mods / "stale.ini").exists())
        self.assertTrue((Path(result["rollback"]) / "ZZMI" / "Mods" / "stale.ini").exists())
        self.assertTrue((self.mods / "DISABLED Old Skin" / "old.ini").exists())
        restored = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(original, restored)
        self.assertTrue(snapshot.verify(self.archive, self.root)["root_matches"])
        self.assertEqual(json.loads(Path(result["journal"]).read_text())["status"], "complete")

    def test_custom_internal_user_config(self):
        self.state.unlink()
        self.state = self.zzmi / "Saved" / "custom.ini"
        self.write(self.state, self.state_bytes)
        self.write(self.zzmi / "d3dx.ini", "[Include]\nuser_config = Saved\\custom.ini\ninclude_recursive = Mods\n")
        self.assertEqual(snapshot.inspect_root(self.root)["user_config"], str(self.state))
        self.make_archive()
        self.state.write_bytes(b"changed")
        snapshot.restore(self.archive, self.root, apply=True)
        self.assertEqual(self.state.read_bytes(), self.state_bytes)

    def test_absolute_internal_config_and_state_staging(self):
        config = {"Importers": {"ZZMI": {"Importer": {"importer_folder": str(self.zzmi)}}}}
        self.write(self.root / snapshot.CONFIG, json.dumps(config))
        self.write(self.zzmi / "d3dx.ini", f"[Include]\nuser_config = {self.state}\ninclude_recursive = Mods\n")
        self.make_archive()
        snapshot.restore(self.archive, self.root, apply=True)
        self.assertEqual(self.state.read_bytes(), self.state_bytes)

    def test_nested_include_relative_to_containing_ini(self):
        self.write(self.mods / "Renamed Skin" / "mod.ini", "[Include]\ninclude = Extra\\nested.ini\n")
        self.write(self.mods / "Renamed Skin" / "Extra" / "nested.ini", "[Constants]\n$x = 1\n")
        snapshot.inspect_root(self.root)
        self.make_archive()

    def test_include_prefix_sections_followed_and_external_refused(self):
        self.write(self.mods / "Renamed Skin" / "mod.ini", "[Include Libraries]\ninclude = Extra\\nested.ini\n")
        nested = self.mods / "Renamed Skin" / "Extra" / "nested.ini"
        self.write(nested, "[Constants]\n$x = 1\n")
        snapshot.inspect_root(self.root)
        self.write(nested, "[IncludePatch]\ninclude = C:\\external.ini\n")
        with self.assertRaises(snapshot.SnapshotError):
            self.make_archive()

    def test_included_user_config_does_not_override_root(self):
        self.write(self.mods / "Renamed Skin" / "mod.ini", "[Include]\nuser_config = nonexistent.ini\n")
        self.assertEqual(snapshot.inspect_root(self.root)["user_config"], str(self.state))

    def test_unrelated_old_ini_external_reference_is_not_active(self):
        self.write(self.root / "Old Backup" / "d3dx.ini", "[Include]\ninclude = C:\\external.ini\n")
        self.make_archive()

    def test_recursive_exclusions_preserved_but_not_audited(self):
        self.write(self.zzmi / "d3dx.ini", "[Include]\ninclude_recursive = Mods\nexclude_recursive = DISABLED*\nexclude_recursive = desktop.ini\n")
        disabled = self.mods / "DISABLED Old Skin" / "old.ini"
        desktop = self.mods / "desktop.ini"
        self.write(disabled, "[Include]\ninclude = C:\\external.ini\n")
        self.write(desktop, "[Include]\ninclude = C:\\external-desktop.ini\n")
        self.make_archive()
        with zipfile.ZipFile(self.archive) as source:
            self.assertEqual(source.read("payload/ZZMI/Mods/DISABLED Old Skin/old.ini"), disabled.read_bytes())
            self.assertEqual(source.read("payload/ZZMI/Mods/desktop.ini"), desktop.read_bytes())
        snapshot.restore(self.archive, self.root, apply=True)
        self.assertTrue(disabled.exists())
        self.assertTrue(desktop.exists())

    def test_explicit_include_bypasses_recursive_exclusion(self):
        self.write(self.zzmi / "d3dx.ini", "[Include]\ninclude_recursive = Mods\nexclude_recursive = DISABLED*\ninclude = Mods/DISABLED Old Skin/old.ini\n")
        self.write(self.mods / "DISABLED Old Skin" / "old.ini", "[Include]\ninclude = C:\\external.ini\n")
        with self.assertRaises(snapshot.SnapshotError):
            self.make_archive()

    def test_missing_or_empty_saved_state_blocks_backup(self):
        for contents in (None, b""):
            with self.subTest(contents=contents):
                if contents is None:
                    self.state.unlink()
                else:
                    self.state.write_bytes(contents)
                with self.assertRaisesRegex(snapshot.SnapshotError, "F10"):
                    self.make_archive()
                self.assertFalse(self.archive.exists())

    def test_ambiguous_root_user_config_blocked(self):
        self.write(self.zzmi / "d3dx.ini", "[Include]\nuser_config = one.ini\nuser_config = two.ini\n")
        with self.assertRaisesRegex(snapshot.SnapshotError, "Ambiguous"):
            snapshot.inspect_root(self.root)

    def test_external_importer_or_dependencies_blocked(self):
        for directive in ("include = ../../outside.ini", "include_recursive = C:\\Outside", "user_config = /outside.ini"):
            with self.subTest(directive=directive):
                self.write(self.zzmi / "d3dx.ini", "[Include]\n" + directive + "\n")
                with self.assertRaises(snapshot.SnapshotError):
                    self.make_archive()
                self.assertFalse(self.archive.exists())
        self.write(self.root / snapshot.CONFIG, json.dumps({"Importers": {"ZZMI": {"Importer": {"importer_folder": str(self.parent / "outside")}}}}))
        with self.assertRaisesRegex(snapshot.SnapshotError, "External"):
            self.make_archive()

    def test_external_resource_or_shader_file_blocked(self):
        for body in ("[ResourceSkin]\nfilename = ../../../../outside.dds\n", "[CustomShaderDo]\nps = C:\\Outside\\shader.hlsl\n"):
            with self.subTest(body=body):
                self.write(self.mods / "Renamed Skin" / "mod.ini", body)
                with self.assertRaises(snapshot.SnapshotError):
                    self.make_archive()

    def test_external_extra_library_blocked(self):
        self.write(self.root / snapshot.CONFIG, json.dumps({"Importers": {"ZZMI": {"Importer": {
            "importer_folder": "ZZMI", "extra_libraries_enabled": True, "extra_libraries": "../external.dll"}}}}))
        with self.assertRaisesRegex(snapshot.SnapshotError, "External"):
            self.make_archive()

    def test_backup_inside_root_and_existing_archive_blocked(self):
        with self.assertRaisesRegex(snapshot.SnapshotError, "outside"):
            snapshot.backup(self.root, self.root / "bad.zip")
        self.make_archive()
        original = self.archive.read_bytes()
        with self.assertRaisesRegex(snapshot.SnapshotError, "overwrite"):
            self.make_archive()
        self.assertEqual(original, self.archive.read_bytes())

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink unavailable")
    def test_source_symlinks_and_root_symlinks_refused(self):
        link = self.root / "linked-state.ini"
        try:
            link.symlink_to(self.state)
        except OSError:
            self.skipTest("symlink creation unavailable")
        with self.assertRaisesRegex(snapshot.SnapshotError, "Symlinks"):
            self.make_archive()
        link.unlink()
        root_link = self.parent / "root-link"
        root_link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(snapshot.SnapshotError, "Symlinks"):
            snapshot.backup(root_link, self.archive)

    def test_unreadable_directory_walk_fails_closed(self):
        def broken_walk(*args, **kwargs):
            kwargs["onerror"](PermissionError("cannot list directory"))
            yield None
        with mock.patch.object(snapshot.os, "walk", broken_walk):
            with self.assertRaisesRegex(snapshot.SnapshotError, "Cannot enumerate"):
                self.make_archive()
        self.assertFalse(self.archive.exists())

    def test_changed_source_aborts_without_publishing(self):
        real = snapshot._zip_info
        modified = False
        def change_during_copy(name, directory=False):
            nonlocal modified
            if name.endswith("d3dx_user.ini") and not modified:
                modified = True
                self.state.write_bytes(b"changed while copying")
            return real(name, directory)
        with mock.patch.object(snapshot, "_zip_info", change_during_copy):
            with self.assertRaisesRegex(snapshot.SnapshotError, "changed"):
                self.make_archive()
        self.assertFalse(self.archive.exists())
        self.assertFalse(list(self.parent.glob("*.partial")))

    def test_added_source_aborts_without_publishing(self):
        real = snapshot._zip_info
        def add_during_copy(name, directory=False):
            if name.endswith("d3dx_user.ini"):
                self.write(self.root / "appeared.txt", "new file")
            return real(name, directory)
        with mock.patch.object(snapshot, "_zip_info", add_during_copy):
            with self.assertRaisesRegex(snapshot.SnapshotError, "changed"):
                self.make_archive()
        self.assertFalse(self.archive.exists())

    def test_hash_tamper_rejected_before_mutation(self):
        self.make_archive()
        self.mutate_archive(lambda name, data: (name, b"tampered") if name.endswith("d3dx_user.ini") else (name, data))
        before = self.state.read_bytes()
        with self.assertRaises(snapshot.SnapshotError):
            snapshot.restore(self.archive, self.root, apply=True)
        self.assertEqual(self.state.read_bytes(), before)
        self.assertFalse(list(self.parent.glob("*.rollback-*")))

    def test_same_size_hash_tamper_rejected(self):
        self.make_archive()
        self.mutate_archive(lambda name, data: (name, bytes([data[0] ^ 1]) + data[1:]) if name.endswith("d3dx_user.ini") else (name, data))
        with self.assertRaisesRegex(snapshot.SnapshotError, "Hash/size"):
            snapshot.verify(self.archive)

    def test_unsafe_archive_paths_rejected_before_mutation(self):
        self.make_archive()
        original = self.archive.read_bytes()
        for name in ("../escape.txt", "payload/../escape.txt", "payload/C:/file", "payload/x:stream", "payload/CON.txt", "payload/trailing.", "payload/back\\slash", "payload/dir//"):
            with self.subTest(name=name):
                self.archive.write_bytes(original)
                with zipfile.ZipFile(self.archive, "a") as out:
                    out.writestr(name, b"evil")
                with self.assertRaises(snapshot.SnapshotError):
                    snapshot.restore(self.archive, self.root, apply=True)
                self.assertEqual(self.state.read_bytes(), self.state_bytes)
                self.assertFalse((self.parent / "escape.txt").exists())

    def test_case_collision_and_symlink_archive_rejected(self):
        self.make_archive()
        original = self.archive.read_bytes()
        with zipfile.ZipFile(self.archive, "a") as out:
            out.writestr("payload/zzmi/D3DX_USER.INI", b"collision")
        with self.assertRaisesRegex(snapshot.SnapshotError, "collid"):
            snapshot.verify(self.archive)
        self.archive.write_bytes(original)
        with zipfile.ZipFile(self.archive, "a") as out:
            info = zipfile.ZipInfo("payload/link")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            out.writestr(info, "../../outside")
        with self.assertRaisesRegex(snapshot.SnapshotError, "Symlink"):
            snapshot.restore(self.archive, self.root, apply=True)

    def test_relocation_restore_refused(self):
        self.make_archive()
        with self.assertRaisesRegex(snapshot.SnapshotError, "original root"):
            snapshot.restore(self.archive, self.parent / "different", apply=True)

    def test_restore_archive_inside_root_refused(self):
        self.make_archive()
        bad = self.root / "bad.zip"
        bad.write_bytes(self.archive.read_bytes())
        with self.assertRaisesRegex(snapshot.SnapshotError, "outside"):
            snapshot.restore(bad, self.root, apply=True)

    def test_stage_failure_keeps_live_root(self):
        self.make_archive()
        original = snapshot._verify_files
        def fail_staging(source, manifest, stage=None):
            if stage is not None:
                raise OSError("simulated disk-full")
            return original(source, manifest, stage)
        with mock.patch.object(snapshot, "_verify_files", fail_staging):
            with self.assertRaisesRegex(OSError, "disk-full"):
                snapshot.restore(self.archive, self.root, apply=True)
        self.assertEqual(self.state.read_bytes(), self.state_bytes)
        self.assertFalse(list(self.parent.glob("*.rollback-*")))
        self.assertFalse(list(self.parent.glob(".*.restore-stage-*")))

    def test_rename_failure_puts_previous_root_back(self):
        self.make_archive()
        self.state.write_bytes(b"live root before restore")
        original = Path.rename
        def fail_stage_rename(path, target):
            if ".restore-stage-" in path.name:
                raise OSError("simulated rename failure")
            return original(path, target)
        with mock.patch.object(Path, "rename", fail_stage_rename):
            with self.assertRaisesRegex(snapshot.SnapshotError, "Previous root was restored"):
                snapshot.restore(self.archive, self.root, apply=True)
        self.assertEqual(self.state.read_bytes(), b"live root before restore")
        journals = list(self.parent.glob("*.restore-*.json"))
        self.assertEqual(len(journals), 1)
        self.assertEqual(json.loads(journals[0].read_text())["status"], "failed-previous-root-restored")

    def test_staged_disk_corruption_detected_before_swap(self):
        self.make_archive()
        original = snapshot._verify_files
        def corrupt_after_write(source, manifest, stage=None):
            result = original(source, manifest, stage)
            if stage:
                (stage / "ZZMI" / "d3dx_user.ini").write_bytes(b"corrupt stage")
            return result
        with mock.patch.object(snapshot, "_verify_files", corrupt_after_write):
            with self.assertRaisesRegex(snapshot.SnapshotError, "on-disk"):
                snapshot.restore(self.archive, self.root, apply=True)
        self.assertEqual(self.state.read_bytes(), self.state_bytes)

    def test_windows_process_query_failure_and_running_other_game(self):
        with mock.patch.object(snapshot.os, "name", "nt"):
            with mock.patch.object(snapshot.subprocess, "run", side_effect=OSError("tasklist unavailable")):
                with self.assertRaisesRegex(snapshot.SnapshotError, "process query failed"):
                    snapshot.guard_processes({})
            query = subprocess.CompletedProcess([], 0, '"StarRail.exe","123","Console","1","5,000 K"\n', "")
            with mock.patch.object(snapshot.subprocess, "run", return_value=query):
                with self.assertRaisesRegex(snapshot.SnapshotError, "StarRail.exe"):
                    snapshot.guard_processes({})

    def test_windows_process_configured_names(self):
        config = {"Importers": {"ZZMI": {"Importer": {"game_process_exe": "CustomGame.exe"}}}}
        with mock.patch.object(snapshot.os, "name", "nt"):
            query = subprocess.CompletedProcess([], 0, '"CustomGame.exe","123","Console","1","5,000 K"\n', "")
            with mock.patch.object(snapshot.subprocess, "run", return_value=query):
                with self.assertRaisesRegex(snapshot.SnapshotError, "CustomGame.exe"):
                    snapshot.guard_processes(config)


if __name__ == "__main__":
    unittest.main()
