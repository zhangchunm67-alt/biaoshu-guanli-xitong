#!/bin/sh
# NAS 标书管理容器（python:3.12-alpine）入口。
# 首次创建容器时安装 LibreOffice、中文字体和 Python 依赖；之后重启直接启动服务。
# 由商机系统 nginx（web 容器 8082）登录后转发，本容器不映射宿主机端口。
set -eu

find_soffice() {
  command -v soffice 2>/dev/null || command -v libreoffice 2>/dev/null || {
    [ -x /usr/lib/libreoffice/program/soffice ] && echo /usr/lib/libreoffice/program/soffice
  } || true
}

if [ -z "$(find_soffice)" ]; then
  # 与采集容器相同：换成清华 Alpine 镜像，保留发行版本与 apk 签名校验。
  sed 's#https://dl-cdn.alpinelinux.org/alpine#https://mirrors.tuna.tsinghua.edu.cn/alpine#' \
    /etc/apk/repositories > /tmp/tengbao-apk.repositories
  apk --repositories-file /tmp/tengbao-apk.repositories add --no-cache \
    libreoffice-writer font-noto-cjk tzdata
fi
BID_LIBREOFFICE="$(find_soffice)"
[ -n "$BID_LIBREOFFICE" ] || { echo 'LibreOffice 安装失败' >&2; exit 1; }
export BID_LIBREOFFICE

# Python 依赖装在持久目录 /deps，按 Python 版本与 requirements.txt 指纹区分；装完整再启用。
deps="/deps/py$(python -c 'import sys; print("%d%d" % sys.version_info[:2])')-$(sha256sum /app/requirements.txt | cut -c1-16)"
if [ ! -f "$deps/.complete" ]; then
  rm -rf "$deps.partial"
  python -m pip install --no-cache-dir --disable-pip-version-check --target "$deps.partial" -r /app/requirements.txt
  touch "$deps.partial/.complete"
  rm -rf "$deps"
  mv "$deps.partial" "$deps"
fi
export PYTHONPATH="$deps"

# 模型密钥等只放在 /secrets/bid.env（NAS 上 600 权限），不写进 compose 或代码目录。
if [ -f /secrets/bid.env ]; then
  set -a
  . /secrets/bid.env
  set +a
fi

umask 077
: "${BID_PROXY_BIND:?需要配置代理私网监听地址}"
exec python -u /app/server.py --host "$BID_PROXY_BIND" --port 8766 --data-dir /data \
  --behind-proxy --portal-link /portal/
