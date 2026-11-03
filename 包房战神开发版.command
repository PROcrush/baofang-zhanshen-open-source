#!/bin/zsh
set -e
task_root="${0:A:h}"
cd "$task_root"
if [[ ! -x "$task_root/.venv/bin/python" ]]; then
  print '尚未安装运行环境，请先按 README.md 完成开发环境准备。'
  exit 1
fi
if [[ ! -f "$task_root/build/native/libbaofang_sync.dylib" ]]; then
  bash "$task_root/scripts/build_native.sh"
fi
exec "$task_root/.venv/bin/python" -m app.main --studio "$@"
