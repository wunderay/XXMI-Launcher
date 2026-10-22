"""Package a prebuilt collector with its corresponding source; Python is build-only."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-revision", default="uncommitted-development-build")
    parser.add_argument("--sdk-version", default="not-recorded")
    args = parser.parse_args()
    tool = Path(__file__).resolve().parent
    repo = tool.parents[1]
    files = {}
    for name in ("XXMI.Capture.exe", "XXMI.Capture.exe.config"):
        files[name] = (args.build_dir / name).read_bytes()
    if files["XXMI.Capture.exe"][:2] != b"MZ":
        raise ValueError("The build is not a Windows executable")
    files["QUICKSTART.txt"] = (tool / "QUICKSTART.txt").read_bytes()
    files["LICENSE.txt"] = (repo / "LICENSE").read_bytes()
    source_names = ["README.md", "QUICKSTART.txt", "Build.cmd", "ModLoadingCollector.csproj",
                    "app.manifest", "package.py", ".gitignore"]
    source_paths = [tool / name for name in source_names]
    source_paths += sorted((tool / "src").glob("*.cs"))
    for pattern in ("*/*.cs", "*/*.csproj", "*/README.md"):
        source_paths += sorted((tool / "tests").glob(pattern))
    for path in source_paths:
        files["Source/tools/ModLoadingCollector/" + path.relative_to(tool).as_posix()] = path.read_bytes()
    files["Source/Themes/Default/window-icon.ico"] = (repo / "Themes/Default/window-icon.ico").read_bytes()
    files["Source/LICENSE"] = files["LICENSE.txt"]
    files["BUILD-INFO.json"] = (json.dumps({
        "application": "XXMI Capture", "version": "0.1.1-preview", "target": "Windows x64 / .NET Framework 4.8",
        "sourceRevision": args.source_revision, "buildSdkVersion": args.sdk_version,
        "qualification": "Cross-compiled in Linux; portable logic tested. Windows UI/WMI/PresentMon/screenshot smoke test pending.",
        "executableSha256": hashlib.sha256(files["XXMI.Capture.exe"]).hexdigest(),
    }, indent=2) + "\n").encode("utf-8")
    files["SHA256SUMS.txt"] = "".join(
        hashlib.sha256(data).hexdigest() + "  " + name + "\n" for name, data in sorted(files.items())
    ).encode("utf-8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + ".tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, data in sorted(files.items()):
                info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                archive.writestr(info, data)
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError("Package verification failed")
            for line in archive.read("SHA256SUMS.txt").decode("utf-8").splitlines():
                digest, name = line.split("  ", 1)
                if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                    raise ValueError("Package digest mismatch: " + name)
        os.replace(temporary, args.output)
    finally:
        if temporary.exists():
            temporary.unlink()
    print(json.dumps({"path": str(args.output), "bytes": args.output.stat().st_size,
                      "sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(), "files": len(files)}, indent=2))


if __name__ == "__main__":
    main()
