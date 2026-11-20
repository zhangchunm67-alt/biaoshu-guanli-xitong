"""Exit 0 only when every pinned package in requirements.txt is installed at its pinned version.

Launchers run this before starting the server so an existing environment picks up
dependencies added in later updates (for example docxcompose), instead of failing
at the first request that needs them.
"""
import re
import sys
from importlib import metadata
from pathlib import Path


def missing(requirements):
    problems = []
    for line in Path(requirements).read_text(encoding='utf-8').splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        match = re.fullmatch(r'([A-Za-z0-9_.\-]+)\s*(?:==\s*([^\s;]+))?.*', line)
        if not match:
            continue
        name, pinned = match.groups()
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            problems.append(f'{name}（未安装）')
            continue
        if pinned and installed != pinned:
            problems.append(f'{name}（已装 {installed}，需要 {pinned}）')
    return problems


if __name__ == '__main__':
    target = sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / 'requirements.txt'
    problems = missing(target)
    if problems:
        print('缺少或版本不符的依赖：' + '、'.join(problems))
        sys.exit(1)
