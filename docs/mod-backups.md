# Mods and saved settings backups

Select **ZZMI**, open **Settings → ZZMI → Mod backups**, and choose **Back up...** or **Restore...**.

This option preserves the **entire XXMI Launcher folder**. It includes every contained importer and its files, so restoring also reverts other games' contained files and global launcher settings. It is available on the ZZMI page because the saved-state and active-dependency checks currently understand ZZMI.

## Why the saved-state file matters

Copying `Mods` alone can lose in-game choices. ZZMI normally stores persistent choices in `d3dx_user.ini` beside `d3dx.ini`, outside `Mods`; `[Include] user_config` can select another file. Many saved variable names also depend on the mod's relative INI path. The backup keeps these files and names together:

| Captured files | Purpose |
| --- | --- |
| `ZZMI/Mods` and its original paths | Mod assets, INIs, namespaces, hotkeys and enabled/disabled names |
| Configured user-state file, normally `ZZMI/d3dx_user.ini` | Saved persistent in-game choices |
| `ZZMI/d3dx.ini`, libraries, shaders and supporting files | The configuration and versions used by those mods |
| `XXMI Launcher Config.json` | Importer paths and launcher preferences |
| Every other ordinary file and directory inside the launcher root | A complete folder restore without leftover or mixed versions |

The native behavior is defined by [user-state path selection and loading](https://github.com/SpectrumQT/XXMI-Libs-Package/blob/946f047def4e915b3790b9b3bc1686a64cf1e8bb/DirectX11/IniHandler.cpp), [ZZMI's save/reset bindings](https://github.com/leotorrez/ZZMI-Package/blob/0f761241b20279849267eb179c7c2a05cff521c4/ZZMI/d3dx.ini), and the launcher's [importer configuration](../src/xxmi_launcher/core/packages/model_importers/model_importer.py).

## Create a backup

1. In ZZZ, set the choices you want and press **plain F10** to save them with the default binding. If you changed the binding, use your configured save/reload key. **Ctrl+Alt+F10 resets settings.**
2. Close ZZZ, other games using this launcher, and mod managers or editors that could change the folder.
3. Open **Settings → ZZMI → Mod backups → Back up...** and choose a new ZIP filename **outside** the launcher folder. An existing archive is never overwritten.
4. Read the full-folder confirmation and select **Back up and close launcher**. Pending settings are saved before preparation. If a game is still running or preparation fails, the launcher stays open with an error.
5. Wait for the completion message before reopening the launcher. Large mod collections can take time to copy and verify.

The backup requires a nonempty saved-state file. It records file contents, relative names, directory membership and modification times, and verifies every archived file with SHA-256. If files change during capture, it refuses to publish that backup. Archives use the same version 1 format as the standalone exact-settings snapshot tool.

## Restore a backup

1. Close all games and other programs editing the launcher folder.
2. Choose **Restore...**, select a backup outside the launcher folder, and confirm its scope. Restores require the **same original launcher location**; moving a setup to a different drive or account is unsupported.
3. The launcher saves pending launcher settings, checks the archive's integrity, prepares a helper, and closes. The helper waits for the launcher process to exit fully before it stages the restore.
4. The helper verifies staged files and dependencies before replacing the active folder. The old folder is retained beside it, for example `XXMI Launcher.rollback-<timestamp>-<id>`. Files added after the backup are kept in that old folder instead of remaining active.
5. Wait for the completion message. It identifies the rollback folder and an external `result.json` report. Reopen XXMI manually when ready.

Cancellation, an invalid archive, a running game, or a preparation failure leaves the launcher open. A staged dependency check, disk error or file lock can still fail after the launcher closes; the result message explains the failure. If the final folder swap fails, the helper attempts to return the previous folder to its original name.

The restore also writes a recovery journal beside the installation. After an interruption, preserve the journal, staging folder and rollback folder until recovery is complete. No rollback folder is automatically deleted. Do not place rollback copies inside `Mods`, where their INIs could be loaded.

## Supported scope

- Ordinary files and directories inside the selected launcher root. Known external dependencies, symlinks, junctions and other reparse points are refused rather than silently omitted.
- The configured ZZMI importer and user-state paths, including custom paths that stay inside the root. Active include checks honor supported recursive exclusions while preserving excluded files in the archive.
- The repository's packaged **PyInstaller onedir** layout at `Resources/Bin`, or source execution with a Python interpreter outside the launcher root. Other compiled layouts fail during preparation. The packaged option does not require a separate Python installation.
- Saved, persistent choices. A backup cannot capture unsaved game memory, infer already-lost choices, or make a changed mod use an obsolete setting.

External game installations, game saves, Windows registry data, NTFS permissions/alternate streams, external manager state and effects of arbitrary custom scripts are outside this folder snapshot's guarantee. Other importers' contained files are included, but their external dependencies are not audited. A game's update can also affect whether an old mod renders correctly after a byte-exact restore.

Allow free space for the backup archive, a complete staged restore and a temporary copy of the launcher runtime. Temporary helper jobs live outside the installation, under the system temporary directory with names beginning `xxmi-mod-state-`. Their result reports are retained. Source helper scripts are removed after completion; copied frozen runtimes remain until they can be removed after the helper has exited. Keep the backup ZIP and any needed rollback folder separately from these temporary jobs.

## Implementation and verification

The [snapshot engine](../src/xxmi_launcher/core/mod_state_snapshot.py) handles inventory, archive validation, saved-state checks and the staged folder swap. The [detached worker](../src/xxmi_launcher/core/mod_state_worker.py) copies its runtime outside the target, verifies its own isolation, pins the initiating process identity, acknowledges readiness and waits for that process to exit. Neither path kills games or launches a restored executable automatically. The GUI blocks other launcher tasks during preparation.

The Linux suite covers snapshot integrity, corruption and path rejection, exact saved-state restoration, rollback failures, real detached source-process backup/restore, UI cancellation and error sequencing, and early entry-point dispatch:

```sh
PYTHONPATH=src/xxmi_launcher python3 -m unittest discover -s tests -q
```

The **Mod backup Windows checks** workflow additionally runs worker tests on Windows, builds the normal onedir payload, exercises the real settings buttons, and runs backup/restore through its actual copied executable using inert temporary fixtures. That packaged test runs only when `XXMI_MOD_STATE_TEST_BUNDLE` explicitly points at the built payload. Synthetic or source-mode passes alone do not qualify the Windows packaged path, and none of these tests establishes an in-game round trip on a user's installed mods.
