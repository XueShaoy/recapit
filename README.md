# Recapit

Recapit 使用 OpenAI 开源的 Whisper 模型在本地把录音转成结构化文字，再由 Codex/Agent Skill 启动一个子 Agent 读取转写检查点并生成总结。整个流程不需要 OpenAI API Key，也不会在 Python 代码中调用远程总结服务。

## 环境

- Python 3.11，由 `uv` 管理
- `ffmpeg` / `ffprobe`
- 已安装并可发现 `.agents/skills/recording-recap`

```bash
uv sync --locked
```

## 三阶段流程

先在本地转写：

```bash
uv run recapit transcribe "records/20260907-shanghai/湖滨路营业部 大会.m4a"
```

转写开始前会显示录音时长、模型、设备和计算精度，以及预计耗时区间。区间来自本机同配置的历史实时率中位数（标明“基于本机历史”），没有历史时则使用保守范围（标明“首次估算，开始后动态校准”）。若无法确认模型已缓存在本机，还会提示首次下载时间不计入上述估算。转写过程中会持续显示活动状态、百分比、音频位置、分段数、已耗时和动态 ETA；交互终端原位刷新，被日志或 Agent 捕获时按最长 30 秒或每 5 个百分点输出一行。默认只输出进度元数据，不把识别正文写入终端；需要预览时显式加上 `--live-text`。

这会生成 `transcript.json`、`transcript.txt` 和 `summary.template.json`。Skill 随后只启动一个子 Agent，让它根据 `transcript.txt` 填写同目录的 `summary.json`。最后确定性渲染：

```bash
uv run recapit render \
  --transcript outputs/湖滨路营业部 大会/transcript.json \
  --summary outputs/湖滨路营业部 大会/summary.json
```

最终 Markdown 包含摘要、关键结论、待办事项和完整转写；最终 JSON 保留原始分段、时间和 Agent provenance。

## 时间码

时间码是可配置的，格式固定为 `[HH:MM:SS]`：

```bash
uv run recapit render --transcript ... --summary ... --timestamps paragraph
uv run recapit render --transcript ... --summary ... --timestamps segment
uv run recapit render --transcript ... --summary ... --timestamps none
```

默认按自然段显示时间码；`none` 只影响 Markdown，JSON 仍保留完整时间。

## 恢复与安全

已有产物默认拒绝覆盖，只有明确需要替换时使用 `--overwrite`。转写完成后即保留检查点，子 Agent 失败时可直接修正或重新生成 `summary.json`，无需再次运行 Whisper：

```bash
uv run recapit prepare-summary outputs/录音/transcript.json
```

总结 JSON 必须包含与 `transcript.json` 相同的 `transcript_sha256`；不匹配时渲染会拒绝写入并保留现有产物。转写内容属于不可信数据，Skill 会将其作为数据而非指令传给子 Agent。

## 配置

```toml
[transcription]
engine = "faster-whisper"
model = "turbo"
language = "zh" # 使用 "auto" 自动检测
device = "auto"
compute_type = "int8"

[output]
directory = "outputs"
timestamps = "paragraph"
paragraph_pause_seconds = 2.0
paragraph_max_chars = 240
```

## 开发检查

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
openspec validate recording-transcription-summary-workflow --strict
```
