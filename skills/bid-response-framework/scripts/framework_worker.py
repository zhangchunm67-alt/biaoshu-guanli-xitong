"""Single service worker: shared Python functions, one parent-enforced timeout."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from framework_service import build_edition_in_process


if __name__ == '__main__':
    try:
        result = build_edition_in_process(**json.load(sys.stdin))
        json.dump(result, sys.stdout, ensure_ascii=False)
    except Exception as error:
        sys.exit(str(error))
