# Serato 只读桥与客屏契约 v1

`python -m probes.serato_reader` 默认20Hz输出JSON行。只使用本机已有 SysV IPC，SHM_RDONLY附接；不创建IPC对象或改变信号量。

成功帧为 `type=serato_frame`、`protocolVersion=1`、`mode=serato`、`producerVerified=true`、`epoch`和递增`sequence`。`capturedNs`、`validUntilNs`采用 CLOCK_MONOTONIC；有效期350ms。两盘`decks`含零基`deck_index`、`source_identity`、`audio_path`、`position_seconds`、`velocity`；`fader`含0..1的`normalized`、`left_deck_index`和`right_deck_index`。

`producerVerified`只代表当前本机进程出生身份、文件、IPC生命周期与schema已核验，不是官方兼容性。`diagnostics.official_compatibility_verified`保持false。身份不再有效时reader不复用上一帧；输出`serato_status`说明等待/失败原因，终止身份会解除本reader附接并稍后重新发现。

控制台严格校验整帧、拒绝重复/乱序，再原子更新播放状态。外部路由与歌曲位置分开。velocity=0只用作暂停观测，不猜暂停时的pitch；负velocity保留显示观测位置，当前不输出歌词。

源文件实际 SHA256 在独立worker计算。验证前使用`pending:`版本且不显示歌词；验证后检查size/mtime/inode/ctime/device，再按SHA版本查时间轴。源总时长未知以`durationMs=0`表示，禁止用未核验 raw length换算或伪造2分钟。歌词自身必须通过版本与时码结构校验。

自动来源：推子<.48取left、>.52取right，中间含边界保持原侧；首帧居中无侧。换生产者epoch清历史。选中侧身份、时钟或歌词未就绪时隐藏，不借另一侧；人工A/B覆盖视频路由，人工隐藏优先。

控制台经临时文件+rename发布`program.json`：精简state、raw和当前版本timelines。独立客屏校验文件，按同一系统时钟重新投影并重查文件proof；控制台或reader失联后TTL到期自行隐藏歌词。bridgeRunning=false立即忽略raw。显示样式完整验证后进入state并本机保存。

当前是独立客屏输出，不包含 Serato 视频纹理，也没有BPM/beat grid/音频声乐增益或灯光字段。
