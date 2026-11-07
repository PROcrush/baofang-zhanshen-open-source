"""Export a curated, history-free source snapshot; never publish or overwrite it."""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

CODE_DIRS = {'app', 'ui', 'native', 'worker', 'tests', 'tools', 'scripts', 'protocol', 'fixtures', 'config'}
ROOT_FILES = {'.gitignore', '.gitleaks.toml', 'CMakeLists.txt', 'PRODUCT.md', 'DESIGN.md',
              'requirements.txt', 'requirements-build.txt', 'requirements-local-asr.txt',
              '包房战神开发版.command', '包房战神Serato只读.command',
              'LICENSE', 'THIRD_PARTY_NOTICES.md', 'CONTRIBUTING.md', 'SECURITY.md'}
DOCS = {
    'macOS使用手册.md', 'macOS构建说明.md', '制作引擎-0.6-接口.md', 'KTV跟唱-20261015.md',
    'DJ常用语与配置-20261103.md', '客屏文字与DJLogo-20261103.md', '公网扫码祝福.md',
    '百炼统一入口-0.11.md', '百炼单首批量与钥匙串-20261023.md', '国内模型API-0.10.md',
    '原版歌词匹配-0.12.md', '本地识别-0.6.md', '字幕预设-20261013.md',
    '简洁界面-20261015.md', '现代模板-20261015.md', '保存与预览修复-20261021.md',
    '歌词走带开关-20261021.md', '现场传统KTV-20261021.md', '现场字幕入退场-20261021.md',
    '现场可靠性修复-20261021.md', '主程序重开现场状态恢复-20261103.md', '客屏崩溃恢复-20261102.md',
}
OVERRIDES = {'README.md': 'docs/open-source/README.md',
             'AGENTS.md': 'docs/open-source/AGENTS.md',
             'probes/README.md': 'docs/open-source/PROBES.md'}
PRIVATE_REPO_URL = 'https://github.com/PROcrush/baofang-zhanshen/'


def selected(name):
    path = Path(name)
    return (name in ROOT_FILES or path.parts[0] in CODE_DIRS or
            path.parts[0] == 'probes' and name != 'probes/latest-serato-report.json' or
            name == 'assets/style_presets.json' or
            path.parts[:2] == ('docs', 'open-source') or
            path.parts[0] == 'docs' and len(path.parts) == 2 and path.name in DOCS)


def export_source(root, destination):
    root, destination = Path(root).resolve(), Path(destination).absolute()
    if destination.is_symlink() or destination == root or root.is_relative_to(destination):
        raise ValueError('目标不能是源目录、其父目录或符号链接')
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('公开导出必须使用新的空目录，不覆盖已有文件')
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
    mapping = {name: name for name in tracked if name and selected(name)}
    for name in ROOT_FILES:
        if (root / name).is_file():
            mapping[name] = name
    mapping.update(OVERRIDES)
    # Templates may be newly prepared before the publication commit.
    for name in OVERRIDES.values():
        mapping[name] = name
    for name in mapping.values():
        source = root / name
        if source.is_symlink() or any(parent.is_symlink() for parent in source.parents if parent != root and parent.is_relative_to(root)):
            raise ValueError('公开源文件不能通过符号链接读取：' + name)
        if not source.is_file():
            raise ValueError('公开模板或源文件缺失：' + name)
    destination.mkdir(parents=True, exist_ok=True)
    records = []
    links = re.compile(r'(!?)\[([^\]]*)\]\(([^)]+)\)')
    for name, original in sorted(mapping.items()):
        source, target = root / original, destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        data = source.read_bytes()
        if target.suffix == '.md':
            value = data.decode('utf-8')
            value = re.sub(r'/Users/[^/\s`\"\']+', '/Users/yourname', value)

            def scrub_link(match):
                image, label, url = match.groups()
                path = url.strip('<>').split('#')[0]
                if url.startswith(PRIVATE_REPO_URL):
                    return label
                if not path or '://' in path or path.startswith(('mailto:', '#')):
                    return match.group(0)
                resolved = (Path(name).parent / path)
                normalized = Path(os.path.normpath(str(resolved))).as_posix()
                return match.group(0) if normalized in mapping else label

            value = links.sub(scrub_link, value)
            data = value.encode('utf-8')
        target.write_bytes(data)
        shutil.copymode(source, target)
        records.append({'path': name, 'sha256': hashlib.sha256(data).hexdigest()})
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    provenance = {'schemaVersion': 1, 'sourceRevision': revision, 'files': records,
                  'excludes': ['private history', 'real diagnostics', 'research reports', 'runtime data',
                               'credentials', 'music', 'downloaded assets', 'models', 'build products']}
    (destination / 'SOURCE_SNAPSHOT.json').write_text(json.dumps(provenance, ensure_ascii=False, indent=2)+'\n')
    return {'destination': str(destination), 'files': len(records), 'sourceRevision': revision}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(export_source(Path(__file__).resolve().parents[1], args.destination), ensure_ascii=False))
