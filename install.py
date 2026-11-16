"""Apply the novel-film extension to a clean, pinned Morpho checkout."""
import argparse
from pathlib import Path
import shutil
import subprocess

BASE = 'b71aed249a42f7f514b0b6a35ef0e5a5161ad5f4'
ROOT = Path(__file__).resolve().parent
OVERLAY = ROOT / 'overlay'
APP_PATCH = ROOT / 'integration/morpho-app.patch'


def install(target, check):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(target), *args], text=True).strip()

    if Path(git('rev-parse', '--show-toplevel')).resolve() != target:
        raise SystemExit('请指定 Morpho 工作树的根目录。')
    if git('rev-parse', 'HEAD') != BASE:
        raise SystemExit(f'请使用 Morpho 基线提交 {BASE} 的独立工作树。')

    paths = sorted(path.relative_to(OVERLAY) for path in OVERLAY.rglob('*') if path.is_file()
                   and '__pycache__' not in path.parts)
    protected = ['infinite_canvas/backend/app.py', *(str(path) for path in paths)]
    if git('status', '--porcelain', '--untracked-files=all', '--', *protected):
        raise SystemExit('目标工作树的影片文件已有改动。请使用新的独立工作树。')
    for relative in paths:
        destination = target / relative
        if not destination.resolve().is_relative_to(target):
            raise SystemExit(f'文件位于工作树之外：{relative}')
        if destination.exists() and not git('ls-files', '--', str(relative)):
            raise SystemExit(f'目标有同名本地文件：{relative}')
    git('apply', '--check', str(APP_PATCH))
    if check:
        return
    git('apply', str(APP_PATCH))
    for relative in paths:
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(OVERLAY / relative, destination)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('morpho', type=Path)
    parser.add_argument('--check', action='store_true', help='只检查接入条件')
    args = parser.parse_args()
    install(args.morpho.resolve(strict=True), args.check)
    print('Morpho 影片扩展检查通过。' if args.check else '已应用 Morpho 影片扩展。')
