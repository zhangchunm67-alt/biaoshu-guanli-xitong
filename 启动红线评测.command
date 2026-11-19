#!/bin/bash
# 双击启动废标红线评测台（只在 127.0.0.1 运行）
cd "$(dirname "$0")" || exit 1

if [ ! -x "./.venv/bin/python" ]; then
  echo "没有找到 .venv/bin/python。"
  echo "请先在仓库根目录按 README 创建虚拟环境并安装 requirements.txt。"
  read -r -n 1 -p "按任意键关闭…"
  exit 1
fi

exec ./.venv/bin/python tools/redline_lab/serve.py "$@"
