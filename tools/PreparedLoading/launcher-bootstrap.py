'''Portable launcher entry point. Self-test never starts Application.'''
import hashlib
import json
import multiprocessing
import os
import runpy
import sys
import traceback
from pathlib import Path


def self_test(root: Path) -> None:
    def deny_external_actions(event, arguments):
        if event in ('socket.connect', 'socket.bind', 'socket.getaddrinfo',
                     'subprocess.Popen', 'os.system', 'os.startfile', 'os.startfile/2'):
            raise RuntimeError(f'External action forbidden during packaging self-test: {event}')

    sys.addaudithook(deny_external_actions)
    manifest = json.loads((root / 'LAUNCHER-BUILD.json').read_text(encoding='utf-8'))
    if manifest['format'] != 1 or manifest['version'] != '0.0.0':
        raise RuntimeError('Unexpected development launcher manifest')
    for relative, expected in manifest['files'].items():
        path = root / relative
        path.resolve().relative_to(root.resolve())
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError(f'Bundle integrity failed: {relative}')

    # Import the real application's dependency graph without constructing it.
    import app
    app.verify_msvc_integrity()
    import core.locale_manager as Locale
    Locale.initialize(root)
    import core.application
    import gui.windows.main.main_window
    import av
    import win32api
    from PIL import Image
    from customtkinter import CTk, CTkLabel, set_default_color_theme
    from tkinterweb import HtmlFrame

    info = win32api.GetFileVersionInfo(str(root / 'XXMI Launcher.exe'), '\\')
    if info['FileVersionMS'] != 0 or info['FileVersionLS'] != 0:
        raise RuntimeError('Expected development executable version 0.0.0.0')
    if not av.library_versions:
        raise RuntimeError('PyAV native libraries unavailable')
    set_default_color_theme(str(root / 'Themes/Default/custom-tkinter-theme.json'))
    image = root / 'Themes/Default/MainWindow/LauncherFrame/eye-hide.png'
    with Image.open(image) as decoded:
        decoded.load()
    window = CTk()
    window.withdraw()
    try:
        label = CTkLabel(window, text=str(Locale.L('packaging_self_test', 'Packaging self-test')))
        label.pack()
        html = HtmlFrame(window, messages_enabled=False)
        html.load_html('<html><body>Offline packaging self-test</body></html>')
        html.pack()
        window.update_idletasks()
    finally:
        window.destroy()


def main() -> None:
    multiprocessing.freeze_support()
    root = Path(sys.executable).resolve().parent
    if sys.argv[1:] == ['--self-test']:
        report = None
        try:
            report_name = os.environ.get('XXMI_LAUNCHER_SELF_TEST_REPORT')
            if report_name:
                report = Path(report_name).resolve()
                if report.parent != root.parent or report.name != 'launcher-self-test.json':
                    report = None
                    raise ValueError('Self-test report must be launcher-self-test.json beside the bundle folder')
            self_test(root)
            if report is not None:
                report.write_text(json.dumps({'format': 1, 'passed': True}, indent=4) + '\n', encoding='utf-8')
        except BaseException:
            failure = traceback.format_exc()
            if report is not None:
                try:
                    report.write_text(json.dumps({'format': 1, 'passed': False, 'traceback': failure}, indent=4) + '\n',
                                      encoding='utf-8')
                except OSError:
                    failure += '\nUnable to write self-test report:\n' + traceback.format_exc()
            if sys.stderr is not None:
                sys.stderr.write(failure)
            # Avoid an unattended PyInstaller exception dialog in Windows CI.
            raise SystemExit(1)
        return
    if '--self-test' in sys.argv[1:]:
        raise SystemExit('Use --self-test without other arguments')
    runpy.run_module('app', init_globals={'__launcher_root__': root}, run_name='__main__')


if __name__ == '__main__':
    main()
