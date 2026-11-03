# 播放位置同步核心

依赖仅限 C++17 标准库，采用稳定的 C ABI，可由 Python `ctypes` 或后续 Qt/C++ 接入。输入必须是同一单调时钟下的纳秒时间戳，位置与时长使用源音频文件的毫秒时间。

```sh
bash scripts/build_native.sh
```

脚本构建 `build/native/libbaofang_sync.dylib`（macOS）或 `.so`（Linux），并运行 `build/native/test_sync`。CMake 可选：`cmake -S . -B build/cmake`，随后 `cmake --build build/cmake` 和 `ctest --test-dir build/cmake`。当前 macOS 脚本产物只包含执行机器的架构，不是通用安装包。

`bf_estimate_position(const BFObservation*, int64_t now_ns, BFEstimate*)` 返回 `0` 表示已分类，`-1` 表示空指针。`BFEstimate.status`：`0` 有效、`1` 过期、`2` 输入无效、`3` 反向播放暂不支持。只有有效状态下 `position_ms` 可以用来选择歌词。其他状态位置统一为零，调用方必须隐藏歌词，不能把零作为真实播放位置。

已暂停时保留锚点位置；正向播放按速度推算位置。有效期包含首尾时间点，超过 `valid_until_ns` 不继续推算。正时长限制位置不超过曲尾；零时长代表未知。负速度明确报告不支持；未知播放状态、未来时间戳、非法有效期、非有限数值及超出时长的锚点均无效。时钟域错误由接入桥负责避免。

`bf_is_discontinuity(const BFObservation*, const BFObservation*, double tolerance_ms)` 返回 `1` 表示新锚点偏移超过容差、`0` 表示连续、`-1` 表示无法比较。它使用新锚点的采集时间推算旧锚点；旧数据已过期时不会误报跳点。调用方只能比较同一音乐版本，换曲应先清空旧锚点。跳点可能是 Hot Cue、Loop 回绕或位置修正，本函数不判断原因。

这里不读取 DJ 软件、不处理音频、不提供真实混音判断。接入桥、屏幕延迟补偿和双盘来源决策由演出协调器负责。
