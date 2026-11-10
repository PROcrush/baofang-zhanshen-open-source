# Serato 只读桥与客屏契约 v1

`python -m probes.serato_reader` 默认20Hz输出JSON行。只使用本机已有 SysV IPC，SHM_RDONLY附接；不创建IPC对象或改变信号量。

成功帧为 `type=serato_frame`、`protocolVersion=1`、`mode=serato`、`producerVerified=true`、`epoch`和递增`sequence`。`capturedNs`、`validUntilNs`采用 CLOCK_MONOTONIC；有效期350ms。两盘`decks`含零基`deck_index`、`source_identity`、`audio_path`、`position_seconds`、`velocity`；`fader`含0..1的`normalized`、`left_deck_index`和`right_deck_index`。

`producerVerified`只代表当前本机进程出生身份、文件、IPC生命周期与schema已核验，不是官方兼容性。`diagnostics.official_compatibility_verified`保持false。身份不再有效时reader不复用上一帧；输出`serato_status`说明等待/失败原因，终止身份会解除本reader附接并稍后重新发现。

控制台严格校验整帧、拒绝重复/乱序，再原子更新播放状态。外部路由与歌曲位置分开。velocity=0只用作暂停观测，不猜暂停时的pitch；负velocity保留显示观测位置，当前不输出歌词。

控制台从新鲜观测取得实际音频路径，后台核验文件。仍有效的文件证明可以复用，等待核验或读取失败时不显示歌词。优先按原文件 SHA-256 查找已有时间轴。原文件摘要未匹配时，仅在解码 PCM 的精确摘要和实际时长都相符且只有一个候选时自动复用。无匹配或有多个候选时提示并隐藏歌词。文件名用于辨认曲库条目。旧缓存的原文件摘要仍可直接匹配，跨标签修改的复用需要先为原来绑定歌词的音频重新核验并补记 PCM 身份。现场自动核验不自动入库或提交识别。

源总时长未知时用 `durationMs=0` 表示。不能用未核验的 raw length 或缓存自身时长代替实际媒体时长。歌词自身必须通过版本与时码结构校验。

自动来源：推子<.48取left、>.52取right，中间含边界保持原侧；首帧居中无侧。换生产者epoch清历史。选中侧身份、时钟或歌词未就绪时隐藏，不借另一侧；人工A/B覆盖视频路由，人工隐藏优先。

控制台经临时文件+rename发布`program.json`：精简state、raw和当前版本timelines。独立客屏校验文件，按同一系统时钟重新投影并重查文件proof；控制台或reader失联后TTL到期自行隐藏歌词。bridgeRunning=false立即忽略raw。显示样式完整验证后进入state并本机保存。

当前是独立客屏输出，不包含 Serato 视频纹理，也没有BPM/beat grid/音频声乐增益或灯光字段。
