#!/bin/zsh
# A finite target-bound native reader for the approved local test copy.
# It does not change developer mode, sudo policy, app signatures or playback.
set -u
script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)" || exit 1
project_dir="${script_dir%/tools}"
test_app="$project_dir/runtime/rekordbox-ab-20261021/rekordbox-test.app"
output_path="$project_dir/runtime/rekordbox-ab-20261021/native-session.jsonl"
cd -- "$project_dir" || exit 1
print 'rekordbox A/B 只读测试：使用仅允许读取测试副本的本地助手。'
print '目标：项目中的已批准测试副本；最长采样 300 秒。'
print '不更改系统开发者模式，不写入或控制 rekordbox 进程。'
print "记录：$output_path"
"$project_dir/.venv/bin/python" -m probes.rekordbox_reader \
  --app "$test_app" --native-helper --duration 300 --interval .1 --output "$output_path" > /dev/null
result_code=$?
print "测试命令结束，退出码：$result_code。日志已保留。"
exit "$result_code"
