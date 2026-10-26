'''Windows x64 directory bundle; deliberately no UAC elevation request.'''
from pathlib import Path
import sys
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

root = Path(SPECPATH).resolve().parents[1]
sys.path.insert(0, str(root / 'src/xxmi_launcher'))
datas, binaries = [], []
# The existing app validates these VC runtime DLLs before normal startup.
# Bundle them explicitly so the user does not need to install build tools.
for name in ('msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll'):
    candidates = (Path(sys.base_prefix) / name, Path(os.environ['SystemRoot']) / 'System32' / name)
    runtime = next((path for path in candidates if path.is_file()), None)
    if runtime is None:
        raise RuntimeError(f'Required redistributable runtime not found: {name}')
    binaries.append((str(runtime), '.'))
hiddenimports = ['app', 'pythoncom', 'pywintypes', 'win32timezone']
for package in ('core', 'gui'):
    hiddenimports += collect_submodules(package)
for package in ('av', 'customtkinter', 'tkinterweb', 'tkinterweb_tkhtml', 'tkinterweb_tkhtml_extras', 'pyglet'):
    package_datas, package_binaries, package_imports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_imports

a = Analysis(
    [str(root / 'tools/PreparedLoading/launcher-bootstrap.py')],
    pathex=[str(root / 'src/xxmi_launcher')],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=['Nuitka', 'nuitka', 'pytest'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='XXMI Launcher',
    console=False,
    debug=False,
    strip=False,
    upx=False,
    uac_admin=False,
    icon=str(root / 'Themes/Default/window-icon.ico'),
    version=os.environ.get('XXMI_LAUNCHER_VERSION_RESOURCE',
                           str(root / 'tools/PreparedLoading/launcher-version.txt')),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='XXMI Launcher')
