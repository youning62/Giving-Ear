#!/bin/zsh
# 双击启动 GivingEar：打开后会自动在浏览器里打开页面，所有操作都在页面里完成。
cd "$(dirname "$0")" || exit 1
if ! command -v uv >/dev/null 2>&1; then
  echo "没有找到 uv。请先安装：https://docs.astral.sh/uv/getting-started/installation/"
  read -k 1 "?按任意键关闭…"
  exit 1
fi
uv run givingear
