## Why

当前项目缺少一条可重复执行的录音处理链路，用户需要手工完成音频转写、文本整理和内容总结，结果格式也难以复用或追溯。引入基于 Whisper 的本地转写与可插拔 AI 总结工作流，可以让指定录音稳定地产出适合阅读的中文文稿及结构化结果。

## What Changes

- 建立由 `uv` 管理的 Python 项目与命令行入口，接收单个录音文件并执行完整工作流。
- 使用兼容 OpenAI 开源 Whisper 模型的转写引擎生成带分段信息的原始转写结果。
- 将 Whisper 短分段整理为可读自然段，并输出整洁的 Markdown 文稿。
- 支持通过 `--timestamps none|paragraph|segment` 配置 Markdown 中时间码的显示粒度，默认使用 `paragraph`。
- 始终输出保留分段起止时间、模型和语言等元数据的 JSON，确保结果可追溯并可在不重新转写的情况下再次排版或总结。
- 由运行 Skill 的 Agent 启动一个子 Agent，读取本地转写结果并生成内容摘要、关键结论与待办事项，不调用额外的模型 API。
- 提供项目内 Skill 编排“本地转写—子 Agent 总结—确定性渲染”，由可测试的 Python CLI 承担音频处理、数据校验和产物生成。

## Capabilities

### New Capabilities

- `recording-transcription`: 校验录音输入、调用 Whisper 模型并生成带时间区间的分段转写。
- `transcript-artifacts`: 整理转写段落，按照可配置时间码策略生成 Markdown，并持久化结构化 JSON。
- `ai-recording-summary`: 由 Skill 委派子 Agent 从完整转写生成结构化摘要，并规定数据交换契约和失败行为。
- `recording-recap-workflow`: 提供由 `uv` 管理的两阶段命令行和 Skill 调用入口，编排本地转写、Agent 总结、格式化与产物落盘。

### Modified Capabilities

无。

## Impact

- 新增 `pyproject.toml`、`uv.lock`、Python 包、测试及项目内 Skill 资源。
- 新增 Whisper 推理和音频处理相关依赖；外部音频解码能力依赖系统 `ffmpeg`，不引入 OpenAI SDK 或其他总结 API 客户端。
- 新增 `outputs/` 产物目录，并读取现有 `records/` 中的录音文件；源录音不会被修改。
- 本地 Whisper 转写不上传原始音频；总结由当前 Agent 环境中的子 Agent完成，不需要额外 API Key。
