#!/usr/bin/env python3
"""Generate a deterministic, realistic synthetic ZZMI install for performance tests.

Layout mirrors a real user folder: ZZMI/d3dx.ini, ZZMI/Core/ZZMI/Libraries (namespaced
libraries), ZZMI/Mods with enabled and DISABLED mods (each with a mod.ini plus .buf/.ib/.dds
resources), a few mods that ship their own copy of a packaged library, and ZZMI/ShaderFixes.
"""
import argparse
import os
import random
from pathlib import Path

LIBS = ["ORFix", "RabbitFX", "SlotFix", "TexFx"]


def write(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode()
    path.write_bytes(data)


def blob(rng: random.Random, size: int) -> bytes:
    # Incompressible-ish data so file systems and caches behave like real assets.
    return rng.randbytes(size)


def mod_ini(rng, name, resources, extra_sections):
    lines = [f"; {name} - generated", "[Constants]", "global persist $swapvar = 0", "global $active = 0", "",
             "[KeySwap]", "condition = $active == 1", "key = VK_DOWN", "type = cycle", "$swapvar = 0,1,2", ""]
    for i in range(extra_sections):
        lines += [f"[TextureOverride{name}Part{i}]", f"hash = {rng.getrandbits(32):08x}", "match_first_index = 0",
                  f"ib = Resource{name}IB{i % max(1, len(resources['ib']))}",
                  "if $swapvar == 0", f"    ps-t0 = Resource{name}Diffuse{i % max(1, len(resources['dds']))}",
                  "endif", "run = CommandListSkinTexture", "checktextureoverride = vb1", ""]
    for kind, files in resources.items():
        for i, fname in enumerate(files):
            label = {"buf": "Buffer", "ib": "IB", "dds": "Diffuse"}[kind]
            lines += [f"[Resource{name}{label}{i}]", "type = Buffer" if kind != "dds" else "",
                      "stride = 40" if kind == "buf" else ("format = DXGI_FORMAT_R32_UINT" if kind == "ib" else ""),
                      f"filename = {fname}", ""]
    return "\n".join(line for line in lines if line is not None) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--mods", type=int, default=200)
    ap.add_argument("--disabled-ratio", type=float, default=0.35)
    ap.add_argument("--scale", type=float, default=0.25, help="Asset size multiplier (1.0 is roughly real size)")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    zzmi = args.root / "ZZMI"
    write(zzmi / "d3dx.ini", "[Loader]\ntarget = ZenlessZoneZero.exe\n[Include]\ninclude_recursive = Mods\n"
          "exclude_recursive = DISABLED*\ninclude = Core\nuser_config = d3dx_user.ini\n")
    for lib in LIBS:
        write(zzmi / "Core" / "ZZMI" / "Libraries" / lib / f"{lib}.ini",
              f"namespace = global\\{lib}\n[CommandList{lib}]\nrun = CommandListSkinTexture\n" * 20)
    for i in range(args.mods):
        disabled = rng.random() < args.disabled_ratio
        name = f"Char{i:04d}"
        folder = ("DISABLED " if disabled else "") + f"{name} Skin"
        base = zzmi / "Mods" / (rng.choice(["", "Agents/", "Agents/Extras/"])) / folder
        res = {"buf": [], "ib": [], "dds": []}
        for kind, count, size in (("buf", rng.randint(3, 6), 400_000), ("ib", rng.randint(1, 3), 150_000),
                                  ("dds", rng.randint(3, 8), 1_500_000)):
            for j in range(count):
                fname = f"{name}{kind.upper()}{j}.{kind}"
                write(base / fname, blob(rng, max(64, int(size * args.scale * rng.uniform(0.3, 1.7)))))
                res[kind].append(fname)
        write(base / "mod.ini", mod_ini(rng, name, res, rng.randint(4, 14)))
        if rng.random() < 0.15:  # variant inis in sub-folders
            write(base / "Variants" / "DISABLED_alt.ini", mod_ini(rng, name + "Alt", res, 2))
        if rng.random() < 0.04:  # mod shipping its own copy of a packaged library
            lib = rng.choice(LIBS)
            write(base / lib / f"{lib}.ini", f"namespace = global\\{lib}\n[CommandList{lib}]\n")
    write(zzmi / "ShaderFixes" / "help.ini", "[Help]\n")
    for i in range(30):
        write(zzmi / "ShaderFixes" / f"{rng.getrandbits(64):016x}-ps_replace.txt", "// shader\n" * 50)
    files = sum(len(f) for _, _, f in os.walk(zzmi))
    size = sum(p.stat().st_size for p in zzmi.rglob("*") if p.is_file())
    print(f"generated {files} files, {size / 1e6:.1f} MB under {zzmi}")


if __name__ == "__main__":
    main()
