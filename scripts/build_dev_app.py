"""Build a workspace-linked macOS development launcher, not a signed release."""
import plistlib
import shlex
from pathlib import Path

root = Path(__file__).resolve().parent.parent
bundle = root / 'build/包房战神.app/Contents'
(bundle / 'MacOS').mkdir(parents=True, exist_ok=True)
with (bundle / 'Info.plist').open('wb') as handle:
    plistlib.dump({'CFBundleName': '包房战神', 'CFBundleDisplayName': '包房战神',
                  'CFBundleIdentifier': 'local.baofangzhanshen.standalone-development', 'CFBundleVersion': '13',
                  'CFBundleShortVersionString': '0.13.0', 'CFBundleExecutable': 'baofang',
                  'CFBundlePackageType': 'APPL', 'NSHighResolutionCapable': True}, handle)
launcher = bundle / 'MacOS/baofang'
launcher.write_text('#!/bin/zsh\nset -e\ncd ' + shlex.quote(str(root)) + '\nexec ' +
                    shlex.quote(str(root / '.venv/bin/python')) + ' -m app.main --studio "$@"\n')
launcher.chmod(0o755)
print(bundle.parent)
