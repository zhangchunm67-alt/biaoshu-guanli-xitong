#!/bin/zsh
set -eu
cd "$(dirname "$0")"
export PYTHONPATH="$PWD/.runtime-deps${PYTHONPATH:+:$PYTHONPATH}"
RUNTIME_PYTHON="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
if [[ -x .venv/bin/python3 ]]; then
  APP_PYTHON="$PWD/.venv/bin/python3"
elif [[ -x "$RUNTIME_PYTHON" ]] && "$RUNTIME_PYTHON" -c 'import docx, PIL, openpyxl' 2>/dev/null; then
  APP_PYTHON="$RUNTIME_PYTHON"
elif python3 -c 'import docx, PIL, openpyxl' 2>/dev/null; then
  APP_PYTHON="$(command -v python3)"
else
  echo '首次启动：正在此目录建立独立运行环境并安装依赖。'
  python3 -m venv .venv
  .venv/bin/python3 -m pip install -r requirements.txt
  APP_PYTHON="$PWD/.venv/bin/python3"
fi
if ! "$APP_PYTHON" -c 'import olefile, pypdf, pypdfium2' 2>/dev/null; then
  echo '正在补齐 Word 导入和原页预览组件。'
  "$APP_PYTHON" -m pip install -r requirements.txt
fi
echo '标书管理系统：http://127.0.0.1:8766'
echo '保留这个窗口以持续运行；按 Control+C 停止。'
export PYTHONDONTWRITEBYTECODE=1
exec "$APP_PYTHON" server.py --open "$@"
