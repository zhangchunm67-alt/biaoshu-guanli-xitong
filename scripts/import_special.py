"""Import the delivered special-library manifest and PNG ZIP, safely resumable."""
import argparse
import json
from pathlib import Path, PurePosixPath
import sys
import tempfile
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import Store


def main():
    parser = argparse.ArgumentParser(description='导入专用材料图片包；重复执行保留已有材料及核对状态。')
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--zip', required=True, type=Path)
    parser.add_argument('--data-dir', type=Path, default=Path(__file__).resolve().parents[1] / 'data')
    args = parser.parse_args()
    entries = json.loads(args.manifest.read_text(encoding='utf-8'))
    store = Store(args.data_dir)
    added = skipped = 0
    try:
        with zipfile.ZipFile(args.zip) as archive:
            names = set(archive.namelist())
            for entry in entries:
                for item in entry['files']:
                    path = PurePosixPath(item['path'])
                    if path.is_absolute() or '..' in path.parts or item['path'] not in names:
                        raise ValueError('图片包缺少合法路径：' + item['path'])
            for entry in entries:
                with tempfile.TemporaryDirectory(prefix='special-import-') as temp:
                    root = Path(temp)
                    for item in entry['files']:
                        target = root / item['path']
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(item['path']) as source, target.open('wb') as output:
                            import shutil
                            shutil.copyfileobj(source, output)
                    mid, created = store.import_special(entry, root)
                    added += bool(created)
                    skipped += not created
                    print(('已导入 ' if created else '已存在 ') + entry['name'], flush=True)
        print(f'完成：新增 {added} 份，已有 {skipped} 份。')
    finally:
        store.close()


if __name__ == '__main__':
    main()
