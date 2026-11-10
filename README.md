# 包房战神 · BaoFang Zhanshen

面向 DJ / KTV 场景的歌词跟随、客屏内容控制与字幕视频制作工具。Python 3.12、PySide6 / Qt Quick、C++17，项目原创源码采用 [MIT](LICENSE)。

这是实验性 macOS 源码发布。当前公开版没有安装包；真实场地、外接电视、DJ 硬件以及不同软件版本需要分别验收。

## 能做什么

- 传统 KTV 跟唱、单行 / 双行字幕、字词扫色、样式与入退场。
- 独立客屏与 Serato Video 透明叠加；支持人工 A/B 接管与歌词隐藏。
- Serato 在后台核验当前音频并复用已有歌词。同一文件版本优先，声音内容和时长一致时也可复用唯一匹配。读不到文件、没有匹配或结果不唯一时，现场会提示并暂不显示歌词。后台核验不会自动入库或提交识别。
- 喊话、Logo、扫码祝福：先在本机预览，明确发布；临时内容到时撤下。
- 个人常用语与姓名变量、最近十次发送历史、便携 DJ 配置导入导出。
- 音频 / 歌词导入、编辑、试听、工程保存与视频导出。
- 新识别使用云端服务，已有时间轴从缓存复用。现场和曲库标注云端识别、本地识别或导入字幕。新提交前需配置服务 Key，核验媒体与费用。
- 曲库的“更多 → 歌词版本”可核对歌词与时码并切换当前版本。已有上屏歌词时，后来收到的不同云端结果进入待审阅。旧词有误时可在识别设置手动审阅重新识别计划。

## 快速启动

建议先在 macOS Apple Silicon、Python 3.12 环境体验无声回放；安装 Xcode Command Line Tools 以编译原生模块。

```sh
git clone https://github.com/PROcrush/baofang-zhanshen-open-source.git
cd baofang-zhanshen-open-source
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
bash scripts/build_native.sh
.venv/bin/python -m app.main --live
```

默认新运行目录进入合成回放，不播放音乐。点击“客屏内容”体验喊话、标识、祝福与“我的”。点击“打开独立客屏”查看输出窗口。先使用自备的无敏感信息素材。

制作工作台：

```sh
.venv/bin/python -m app.main --studio
```

应用构建见 [macOS 构建说明](docs/macOS构建说明.md)。

## DJ 接入边界

| 路线 | 当前范围 |
| --- | --- |
| 合成回放 | 默认体验和隔离测试，不产生真实音频 |
| Serato | 实验性只读接入与 Video 输出叠加，需匹配本机软件 / 进程 / IPC 条件 |
| rekordbox 原版 AX | 本机已验证版本的标题 / 时间读取，需命名助手辅助功能权限，手动确认歌曲和 A/B |
| rekordbox 测试副本 | 仅针对既有特定版本的实验代码，不提供任何第三方测试副本或修改过的应用 |

接入不控制 DJ 音频。没有匹配版本、有效来源或新鲜播放观测时隐藏歌词；客屏打开不等于电视已正确显示。不要直接把演示或单元测试结果当作演出验收。

## 文档与测试

- [使用手册](docs/macOS使用手册.md)
- [客屏文字与 Logo](docs/客屏文字与DJLogo-20261103.md)
- [公网扫码祝福与中继部署](docs/公网扫码祝福.md)
- [常用语、历史和 DJ 配置](docs/DJ常用语与配置-20261103.md)
- [第三方许可](THIRD_PARTY_NOTICES.md) · [贡献指南](CONTRIBUTING.md) · [安全反馈](SECURITY.md)

```sh
PYTHONPATH=.:tests .venv/bin/python -m unittest discover -s tests
PYTHONPATH=.:tests .venv/bin/python -m tools.check_screen_content
PYTHONPATH=.:tests .venv/bin/python -m tools.check_screen_personal
```

界面检查使用隔离运行目录与合成数据。源码发布前在开发环境通过 810 项单测；请在自己的系统复验。Linux 原生同步库有引导脚本，但完整桌面应用、Windows 和各类 DJ 硬件不作兼容承诺。

## 数据与授权

开发版数据在项目 `runtime/`，发行应用使用独立用户数据目录。歌曲、Key、模型权重、运行数据、云任务回执、真实诊断快照、内部联调历史和安装包不随此仓库发布。不要把自己的运行目录或媒体提交到 Git。

本仓库采用干净的首次公开快照，后续公开更新保留提交历史。MIT 仅覆盖项目原创内容；使用或分发第三方依赖和媒体时仍需遵守各自许可。
