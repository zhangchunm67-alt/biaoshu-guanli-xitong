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
if [[ ! -f .env.oray ]]; then
  echo '未找到 .env.oray。请复制 .env.oray.example 为 .env.oray，填写花生壳域名与访问口令。'
  echo '示例：cp .env.oray.example .env.oray'
  exit 1
fi
# shellcheck disable=SC1091
set -a
source .env.oray
set +a
if [[ -z "${ORAY_PUBLIC_HOST:-}" || -z "${ORAY_ACCESS_TOKEN:-}" ]]; then
  echo '.env.oray 须设置 ORAY_PUBLIC_HOST 与 ORAY_ACCESS_TOKEN'
  exit 1
fi
PORT="${ORAY_PORT:-8766}"
INTRANET_PORT="${ORAY_INTRANET_PORT:-$((PORT + 1))}"
echo "本机材料入口：http://127.0.0.1:${INTRANET_PORT}/ （仅本机，勿映射到花生壳）"
echo "花生壳映射口：127.0.0.1:${PORT}"
echo "花生壳域名：http://${ORAY_PUBLIC_HOST}/ （请在花生壳客户端映射到 127.0.0.1:${PORT}）"
echo '外网可编制标书；营业执照、身份证等材料原件与含材料导出仅限本机材料入口。'
echo '已启用防空闲休眠；合盖、手动睡眠或退出花生壳后外网会断开。'
echo '保留这个窗口以持续运行；按 Control+C 停止。'
export PYTHONDONTWRITEBYTECODE=1
exec /usr/bin/caffeinate -i "$APP_PYTHON" server.py --host 0.0.0.0 --port "$PORT" \
  --intranet-port "$INTRANET_PORT" \
  --public-host "$ORAY_PUBLIC_HOST" --access-token "$ORAY_ACCESS_TOKEN" "$@"
