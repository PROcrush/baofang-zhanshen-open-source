#!/usr/bin/env bash
set -euo pipefail

task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
build_dir="${task_root}/build/native"
compiler="${CXX:-c++}"
mkdir -p "${build_dir}"

common_flags=(-std=c++17 -Wall -Wextra -Werror -pedantic -O2
              -DBF_SYNC_BUILD -I "${task_root}/native/include")
case "$(uname -s)" in
    Darwin)
        library="${build_dir}/libbaofang_sync.dylib"
        shared_flags=(-dynamiclib -fPIC -fvisibility=hidden)
        ;;
    Linux)
        library="${build_dir}/libbaofang_sync.so"
        shared_flags=(-shared -fPIC -fvisibility=hidden)
        ;;
    *)
        printf 'Unsupported platform for this bootstrap build: %s\n' "$(uname -s)" >&2
        exit 1
        ;;
esac

"${compiler}" "${common_flags[@]}" "${shared_flags[@]}" \
    "${task_root}/native/src/baofang_sync.cpp" -o "${library}"
"${compiler}" "${common_flags[@]}" \
    "${task_root}/native/src/baofang_sync.cpp" \
    "${task_root}/tests/native/test_sync.cpp" -o "${build_dir}/test_sync"
"${build_dir}/test_sync"
printf 'Built: %s\n' "${library}"

if [[ "$(uname -s)" == Darwin ]]; then
    overlay_library="${build_dir}/libbaofang_overlay.dylib"
    overlay_flags=(-std=c++17 -Wall -Wextra -Werror -pedantic -O2 -fobjc-arc
                   -I "${task_root}/native/include")
    "${compiler}" "${overlay_flags[@]}" -dynamiclib -fPIC -fvisibility=hidden \
        "${task_root}/native/src/baofang_overlay.mm" \
        -framework AppKit -framework CoreGraphics -o "${overlay_library}"
    "${compiler}" "${overlay_flags[@]}" \
        "${task_root}/native/src/baofang_overlay.mm" \
        "${task_root}/tests/native/test_overlay.mm" \
        -framework AppKit -framework CoreGraphics -o "${build_dir}/test_overlay"
    "${build_dir}/test_overlay"
    c_compiler="${CC:-cc}"
    "${c_compiler}" -std=c99 -Wall -Wextra -Werror -pedantic -O2 \
        -I "${task_root}/native/include" \
        "${task_root}/tests/native/test_overlay_c.c" "${overlay_library}" \
        -o "${build_dir}/test_overlay_c"
    "${build_dir}/test_overlay_c"
    printf 'Built: %s\n' "${overlay_library}"
fi
