# Download withdrawn: reported Defender detection

The user reports Chrome blocking the original MSI and Microsoft Defender naming
`Trojan:Script/Wacatac.B!ml`. The affected nested file and Defender definition
version are not yet known. The detection has not been independently reproduced
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
