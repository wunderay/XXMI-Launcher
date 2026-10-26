# Download withdrawn: reported Defender detection

The user reports Chrome blocking the original MSI and Microsoft Defender naming
`Trojan:Script/Wacatac.B!ml`. The affected resource is the exact original MSI URL;
no nested file or local Defender definition version was supplied. The detection has not been independently reproduced
or adjudicated, and is not being classified as a false positive.

The download recommendation is withdrawn. Keep the file quarantined; do not
restore/allow it or disable antivirus protection. The binary is removed from the
current branch. Historical Git objects and CI evidence remain available for
investigation; their availability is not a recommendation to run the file.

Affected artifact:

- MSI: `XXMI-Launcher-Installer-Online-v2.4.10.msi`
- Size: 54,475,945 bytes
- SHA-256: `d6076f7200521a661b6f2d34d1759ab86a953dc7a2fb0e61343249d27e558781`
- Producer: `1202392c4aea7a682da81e63b8e41b6c98fbcc07`
- Manifest EXE SHA-256: `cdb6ed8967c8c589b4014b44ceaf2a50821ff0ba82a1c133e2620f7f8d7f6bdb`

Prior CI established functional behavior, source/payload integrity and an inert
installer lifecycle. It did not run an antivirus acceptance gate. The original
build/validation receipts remain unchanged and do not establish malware safety.
The derived verification record now explicitly records the unresolved report.

Investigation must inspect the exact original MSI/payload, record scanner engine
and signature versions and detections, and distinguish source/provenance review
from an antivirus verdict. Rebuilding or renaming to obtain a different hash is
not a resolution. The original payload is an unsigned PyInstaller directory
bundle with UPX disabled. That fact alone does not explain or dismiss the alert.

## Exact-artifact CI diagnostic

[Diagnostic run 40272769199](https://github.com/wunderay/XXMI-Launcher/actions/runs/40272769199)
passed at scanner source `e3c29e0cccfe98907156e0e74d8de9a61e65c627`.
The MSI SHA-256 matched before and after the custom scan. Microsoft-signed
Defender platform `4.18.26090.9`, engine `1.1.26090.9`, with at-scan definitions
`1.461.208.0`, exited 0 and explicitly reported “found no threats.” The
signature-update command succeeded. Windows PowerShell 5.1 regression checks
passed for exit codes 0/2, both output streams and owned-process timeout handling.

**This does not establish a false positive or antivirus clearance.** The hosted
runner had real-time protection off, cloud reporting off (`MAPSReporting=0`),
sample submission set to never (`SubmitSamplesConsent=2`) and Block at First
Seen disabled. These settings were unchanged. The on-demand custom scan did not
reproduce Chrome's download or the user's local/cloud protection conditions.
The download recommendation remains withdrawn.

[Original diagnostic records](defender-e3c29e0/provenance.json) retain the report,
stdout/stderr and regression results. The downloaded archive SHA-256 was checked
and an independent review found no evidence mismatch. These records contain no
installer binary. The earlier failed attempt stopped before downloading because
PowerShell returned a missing process exit code; the wrapper was fixed and tested.

A [Microsoft classification-review dossier](MICROSOFT-REVIEW.md) is prepared.
It has not been submitted. Sending the sample/report to Microsoft requires
explicit authorization for that external submission.
