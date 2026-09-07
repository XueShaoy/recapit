## Validation Run

- 日期：2026-09-07
- 输入：`records/20260907-shanghai/湖滨路营业部 大会.m4a`
- 文件大小：16,041,839 bytes；时长：1,872.619 秒
- Whisper：`faster-whisper 1.2.1`，`small`，CPU `float32`
- 转写结果：1,404 个有序分段；源录音未被修改
- 产物：`/private/tmp/recapit-real-validation/湖滨路营业部 大会/transcript.json`

本次验收采用三阶段流程：本地 Whisper 转写、一个 Agent 子任务生成带哈希的 `summary.json`、本地确定性渲染。没有配置或调用 OpenAI API Key/API。Agent 输入为同目录的 `transcript.txt` 与 `summary.template.json`，最终总结保留模板中的 `transcript_sha256`。

已验证：

- `summary.json` 严格 schema 校验、哈希匹配和 Agent provenance；哈希不匹配时不修改既有 Markdown/JSON。
- `paragraph` 渲染生成 44 个 `[HH:MM:SS]` 时间码；`none` 渲染生成 0 个时间码。
- Markdown 包含“AI 摘要、关键结论、待办事项、完整转写”四个章节。
- `uv` 锁文件已移除 OpenAI SDK；Ruff、mypy、pytest、Skill 校验和 OpenSpec 严格校验通过。

## Findings

- 默认 `small/default` 在当前 CPU 后端使用 `float32`，准确性路径可用，但长录音耗时明显。
- `transcript.json` 可独立支持重新生成总结和切换时间码，不需要再次运行 Whisper。
- 子 Agent 失败时保留转写检查点，Skill 不回退到外部总结服务。
- 后续硬件检查确认当前 Apple M3/24GB 环境支持 `turbo`；项目默认值已切换为 `turbo` + CPU `int8`。模型已在本机成功加载，并对真实录音的 15 秒切片完成推理（中文、13 个分段）。上方完整录音指标仍是切换前 `small/float32` 的基线，不混写为 turbo 全量验收结果。
