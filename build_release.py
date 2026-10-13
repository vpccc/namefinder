"""Build a source release using an explicit public-file allowlist."""
from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

BASE = Path(__file__).resolve().parent
FILES = (
    'app.py', 'core.py', 'ubisoft.py', 'desktop.py', 'start.bat', 'debug.bat',
    'requirements.txt', 'README.md', 'LICENSE', 'SECURITY.md', 'PUBLISHING.md',
    '.gitignore', 'build_release.py',
    'tests/test_core.py', 'tests/test_discord_mode.py', 'tests/test_words_ubisoft.py',
    'tests/test_desktop.py', 'tests/test_security.py',
    'ui/index.html', 'ui/style.css', 'ui/ui.js', 'ui/icon.svg',
    'dictionary/english.txt', 'dictionary/README.md',
    'dictionary/LICENSE_en_US.txt', 'dictionary/LICENSE_en_GB-ise.txt',
)


def build():
    target = BASE / 'dist' / 'Namefinder-v7.1-source.zip'
    target.parent.mkdir(exist_ok=True)
    # Stable metadata: no local paths, usernames or local modification times.
    with ZipFile(target, 'w', compression=ZIP_DEFLATED) as archive:
        for relative in FILES:
            info = ZipInfo('Namefinder/' + relative, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, (BASE / relative).read_bytes())
    return target


if __name__ == '__main__':
    print(build())
