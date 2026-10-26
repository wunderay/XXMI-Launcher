# Prepared Microsoft malware-classification review

Status: prepared for review; **not submitted**. No false-positive conclusion is
asserted. The download recommendation remains withdrawn.

Please investigate the reported Microsoft Defender classification
`Trojan:Script/Wacatac.B!ml` for this exact contributor-built XXMI Launcher MSI:

- File: `XXMI-Launcher-Installer-Online-v2.4.10.msi`
- Size: 54,475,945 bytes
- SHA-256: `d6076f7200521a661b6f2d34d1759ab86a953dc7a2fb0e61343249d27e558781`
- Producer source: `1202392c4aea7a682da81e63b8e41b6c98fbcc07` in
  `https://github.com/wunderay/XXMI-Launcher`
- Original build: `https://github.com/wunderay/XXMI-Launcher/actions/runs/40268228471`
- Manifest launcher EXE SHA-256:
  `cdb6ed8967c8c589b4014b44ceaf2a50821ff0ba82a1c133e2620f7f8d7f6bdb`

The user reports that Chrome blocked downloading the MSI and Windows Security
named the classification above. The affected resource supplied was the original
immutable MSI URL recorded in the diagnostic report, without a nested filename.
No local engine/definition version or sample execution evidence was supplied.

The build is an unsigned PyInstaller 6.20.0 directory bundle, UPX disabled, in an
original WiX 3 MSI. The MSI contains the launcher, Python/native runtime, Themes,
Locale and license. XXMI's separate mod-loader/native packages are downloaded
through its existing authenticated update path. Source review found intentional
mod-injection functionality; the corresponding `3dmloader.dll` and `d3d11.dll`
were not present in this MSI's manifest. The installer has standard WiX UI and
directory-property actions and does not automatically launch the application.

Retained validation matches all 1,801 embedded file hashes. Dependency versions
were pinned, but original build inputs were not protected by a complete
pre-download hash lock. Source/provenance and functional checks therefore are
not presented as evidence that the compiled sample is malware-free.

A custom Defender scan of the exact MSI reported no threats:

- Diagnostic: `https://github.com/wunderay/XXMI-Launcher/actions/runs/40272769199`
- Scanner source: `e3c29e0cccfe98907156e0e74d8de9a61e65c627`
- Platform `4.18.26090.9`, engine `1.1.26090.9`, definitions `1.461.208.0`
- Exit 0; original SHA-256 matched before/after; no sample execution/extraction
- CI real-time protection off, cloud reporting off, never-submit sample policy,
  Block at First Seen disabled; no protection settings changed

That scan does not reproduce the user's browser/local/cloud protection context.
Please identify the detected component or container member, reproduce the
reported classification under the applicable protection model, and adjudicate
the sample. An absence of detections in this limited scan is not a request to
override the user's protection without malware analysis.

[Diagnostic evidence and limits](SECURITY-STATUS.md) accompany this dossier.
The intended destination is Microsoft's official
[file-submission service](https://www.microsoft.com/en-us/wdsi/filesubmission).
Any submission must be explicitly authorized and must not include local account,
contact or other private information without separate authorization.
