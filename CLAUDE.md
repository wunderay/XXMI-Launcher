# XXMI-Launcher

- The launcher is Windows-only; the GUI cannot run on Linux.
- Fast check (Linux, needs `pip install cryptography psutil`):
  `PYTHONPATH=src/xxmi_launcher python3 -m unittest discover -s tests -q`
  Expect OK with about 27 Windows-only skips. Without `PYTHONPATH`, 3 modules fail with `No module named core`.
- CI runs the same command in `.github/workflows/linux-tests.yml` (PRs and main).
- Exact-byte evidence: never edit, re-encode, normalize line endings in, or regenerate tracked binaries, or `.gitattributes`-protected files
  (e.g. `tools/PreparedLoading/native.patch`). Hashes and MSI/payload bytes are verified exactly.
- Development builds are no longer committed: `development-builds/` is untracked and gitignored, and
  `.github/workflows/prepared-loading-checks.yml` publishes them to the rolling `development` prerelease
  (Releases page). Earlier builds remain in git history.
