# 国内模型 API 0.10

2026-10-14。顶部新增“模型 API”，语音识别和文字纠错分别配置；支持当前制作歌曲与A/B盘，模型处理在独立工作进程中执行。

## 内置配置

| 功能 | 服务 | 默认模型 | 处理方式 |
| --- | --- | --- | --- |
| 语音识别 | 火山引擎豆包语音 | volc.bigasr.auc_turbo | 音频Base64，X-Api-Key，采纳实际句/词时间码 |
| 演唱字幕 | 302豆包 | doubao-vc-singing | 复用原上传、提交、回执及续查链路 |
| 语音转写 | 硅基流动 | FunAudioLLM/SenseVoiceSmall | 文件上传，返回文字，缺时间码时不绑定同步歌词 |
| 文字纠错 | 通义千问 | qwen-plus | 百炼国内或北京工作空间地址，关闭思考 |
| 文字纠错 | DeepSeek | deepseek-flash | 官方Chat Completions，关闭思考 |
| 文字纠错 | 豆包方舟 | doubao-seed-2-1-pro-260915 | 支持Model ID/ep接入点，使用max_completion_tokens |
| 文字纠错 | 智谱GLM | glm-4.7-flash | 普通模型API，4.5–4.7系列关闭思考 |
| 文字纠错 | 硅基流动 | deepseek-ai/DeepSeek-V4-Flash | 保留完整模型命名空间，关闭思考 |
| 自定义 | OpenAI兼容文字/语音 | 用户填写 | HTTPS；本机localhost可使用HTTP |

这些是可编辑的配置预设，账户是否开通、额度及服务效果通过实际调用确认。火山语音Key和方舟Key按两个服务配置，不混用。自定义语音只采纳实际返回的segments；没有时间码时保存参考文字。

## 使用

1. 进入“模型 API”，选择语音识别或文字纠错，再选服务商。
2. 填写该服务的Key，检查控制台已开通的模型ID和地域，点击保存。配置JSON只保存地址、模型等非密钥字段；Key仅在本次运行使用。
3. 语音点击“核验配置”只做本机检查；文字点击“测试连接”发送短测试文本，可能产生少量服务用量。
4. 选择当前制作歌曲或A/B盘。识别先点击“预检歌曲与上传计划”，核对文件和服务后确认上传。直接上传支持25MB内WAV/MP3/OGG；其他格式需先转换。302保持50MB限制及本机共享额度预留。
5. 识别结果预览后应用；纯文字可保存为TXT，不能直接用于同步客屏。
6. 文字纠错点击“生成纠错建议”，只发送歌词文字。逐句勾选后应用，制作页可撤销；歌词句时码不改，改词的原逐字对齐不继续复用。切歌或编辑后旧结果不能覆盖当前内容。

旧302配置文件可通过“导入旧302配置”读取用户主动选择的文件。地址不能夹带账号、Key或查询参数；内置服务商不接受其他主机，其他网关通过自定义接口明确配置。不同服务商和不同Base URL的Key分别暂存，不能隐式转发原Key。

## 请求与缓存边界

- 语音提交先保存意向，后台同一计划持有文件锁；超时或取消不自动重复上传。302任务与现有曲库准备使用相同回执及共享额度，续查仍绑定原路线和版本。
- 文字纠错要求模型返回原句数和原顺序；空句、超长句、截断或不合法JSON被拒绝。只将文字建议纳入人工审阅，模型不接触时间码。
- 应用前再次核验音频版本和原歌词摘要。A/B盘结果经原SQLite/离线缓存机制发布；制作结果进入自己的工程及撤销链。
- 离线演出阻止测试、识别和纠错请求，并终止本机正在处理的AI进程；已受理的云任务可能继续。已有本地歌词仍可使用。
- Key不写入配置、任务JSON或参数，不发送给后台计划预检；网络工作进程只获得当前任务的Key，不继承其他云凭据。HTTP错误不回显服务响应里的凭据。

## 验证

- 148项核心及回归测试：国内协议参数、Base URL规范化、主机和Key隔离、服务时间码、无时间码转写、文字结构验证、审阅/勾选/应用、旧编辑防覆盖、离线阻断、302续查及共享预算、原离线/批次/识别链。
- Qt操作结果：1000×700与1440×900配置入口，真实QML按钮及密码字段清空，识别预检与审阅应用，逐句纠错选择和撤销，离线禁用。使用合成媒体和模拟服务响应。
- macOS原生操作日志：同一Qt流程在原生窗口完成，无QML警告。输入法系统消息未影响完成。
- 正常开发版更新至0.10.0并实际重启；在原生主窗口打开了新API页。没有读取或测试真实Key，没有上传用户歌曲，云请求为0。

完成的是接口实现与本机流程。真实Key、账户权限、网络可达性、歌唱识别准确率、AI纠错效果和客屏同步需实际歌曲验证。

## 官方依据

- [豆包录音极速识别](https://www.volcengine.com/docs/6561/1631584)：请求地址、Base64本地音频、X-Api-Key与资源ID、状态码和时间码响应。
- [硅基流动语音转写](https://docs.siliconflow.cn/docs/api/audio-transcriptions-post)：文件/model参数及文字响应。
- [通义千问OpenAI兼容接口](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)：Base URL与模型调用；[工作空间与结构化输出](https://help.aliyun.com/zh/model-studio/qwen-structured-output)。
- [DeepSeek首次调用](https://api-docs.deepseek.com/zh-cn/)与[对话参数](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion/)：当前模型名称、URL与请求字段。
- [豆包方舟兼容接口](https://docs.volcengine.com/docs/ark/compatible-with-openai-sdk?lang=zh)及[输出控制](https://docs.volcengine.com/docs/ark/context-management?lang=zh)：地域、Model/ep与max_completion_tokens。
- [智谱对话补全](https://docs.bigmodel.cn/api-reference/模型-api/对话补全)及[硅基流动对话补全](https://docs.siliconflow.cn/docs/api/chat-completions-post)：模型、鉴权及思考参数。

语音配置

文字纠错
