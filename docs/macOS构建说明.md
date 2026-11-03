# macOS 0.13.0 自包含测试包

目标为 Apple Silicon arm64，最低系统声明 macOS 15.0，实际验收系统为 macOS 26.6.2。最低版本依据本包 PySide6/shiboken 绑定的 Mach-O 15.0 要求，不能只按 Qt framework 或 Python 的最低版本决定。使用独立 CPython 3.12.14 与已安装的项目依赖，保留真实 Python 子进程能力，避免冻结后 `sys.executable -m` 再启动主界面的问题。原理参考 [PyInstaller 对冻结解释器路径的说明](https://pyinstaller.org/en/stable/runtime-information.html)；本方案不使用 PyInstaller。

## 构建

在项目 Python 3.12 环境安装 `requirements.txt`、`requirements-local-asr.txt`、`requirements-build.txt`，并准备官方 tiny 模型到 `assets/models/tiny.pt`。模型 SHA256 必须为：

```text
65147644a518d12f04e32d6f3b26facc3f8dd46e5390956a9424a650c0ce22b9
```

```sh
.venv/bin/python scripts/build_macos.py --archives
# 仅源代码/手册变化，可复用上一次生成的私有 Python 运行时：
.venv/bin/python scripts/build_macos.py --reuse-runtime --archives
```

构建需要 Xcode 命令行工具；用户安装后不需要。输出：

- `dist/包房战神-0.13.0-macOS-arm64.dmg`：拖拽安装镜像。
- 同名 ZIP：包含应用、离线 HTML/Markdown 使用手册、构建信息和第三方组件清单。
- `dist/包房战神-0.13.0-macOS-arm64-SHA256SUMS.txt`：两个归档的 SHA256。
- 独立 `dist/使用手册.html` 与 `dist/使用手册.md`。

暂存应用默认在用户 `Library/Caches/包房战神/build/`。使用 `--output-root` 可指定其他本地非同步暂存目录。不要在会自动重新添加 FinderInfo 的 Documents/FileProvider 目录内直接签名；这会使 `codesign` 报资源元数据冲突。`--reuse-runtime` 不更新第三方依赖，依赖变化后须完整重建。

当前仅对本产品新建包做 ad-hoc 签名，校验 `codesign --verify --deep --strict`。没有 Developer ID 签名或 Apple 公证；不改变原版 rekordbox、已有开发助手、系统权限或 Gatekeeper 设置。仓库和运行数据都不自动发布为 GitHub Release，代码按项目约定同步原私有远端。

## 资源与运行边界

- `Contents/MacOS/baofang` 是原生启动器，定位相对 Resources 下的 Python 和程序，不记录命令参数或凭据。正常 Finder 启动的错误日志在 `~/Library/Logs/包房战神/application.log`。
- `Contents/Resources/python` 自带 Python、Qt、音频与识别依赖；Python 模块加载关闭用户 site，主进程与全部白名单 worker 从启动阶段禁止写字节码。Numba 等缓存定向到用户 `Library/Caches/包房战神`。
- `Contents/Resources/app` 白名单收集应用源码、QML、合成演示、样式、原生库、tiny 模型和预编译 AX 助手；不收集项目 `runtime`、歌曲、Key、历史探针报告或开发依赖路径。
- `packaged.json` 标记产品布局、版本、源提交、是否存在未提交修改、模型/FFmpeg/助手摘要和未公证状态。运行时只允许本产品固定位置的模型与 FFmpeg，继续拒绝借用其他应用包的资源。
- 用户数据默认在 `~/Library/Application Support/包房战神`，`--runtime` 可指定隔离验收目录。第一次打开不导入开发版曲库或凭据；完整迁移说明在使用手册中。
- 包内保留依赖自身的 LICENSE/COPYING 文件，另列第三方名称、版本和元数据许可字段。完整依赖来自当前经测试的 `.venv`，构建后检查组件清单；更换依赖需重新验证。

## 封包验收

```sh
.venv/bin/python tools/check_macos_package.py \
  --bundle '/本地暂存目录/包房战神.app' \
  --work-dir '/private/tmp/新的封包验收目录'
```

该工具使用包内 Python、最小环境和禁止网络的 macOS 沙盒，实际运行 Qt 子进程：合成音频导入、字幕导入、工程保存/重开、视频导出、完整音视频解码、原生窗口截图。另核对原生库、模型与 FFmpeg 摘要，逐文件比较运行前后包内容，任何新写入的字节码或资源变化都失败。源音频保持不变；不读取真实 Key、不授权 AX、不上传歌曲。

验收记录包含 `report.json`、worker 报告、截图、成片和分步日志。应用包成功运行不等于真实百炼新任务、真实 Key 重启恢复、其他 macOS、实体控制器、场地电视或演出稳定性通过；这些沿用各专项验收边界。
