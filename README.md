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

录音默认按 15 分钟核心区间处理，每块带前后各 10 秒边界上下文；同一时刻只提取一个临时 WAV，并在每块成功后写入检查点。30–60 分钟录音因此通常包含 2–4 个 chunk，中断后默认从第一个未完成 chunk 继续，不重复处理已经提交的部分。

默认输出目录为 `<录音名>-<源文件SHA256前12位>`，其中包含 `run.json`、`chunks/`、不可变的 `transcript.json`、`transcript.txt` 和 `summary.template.json`。Skill 随后只启动一个子 Agent，让它根据 `transcript.txt` 填写同目录的 `summary.json`。最后确定性渲染：

```bash
uv run recapit render \
  --transcript "outputs/湖滨路营业部 大会-<源摘要>/transcript.json" \
  --summary "outputs/湖滨路营业部 大会-<源摘要>/summary.json"
```

最终 Markdown 包含摘要、关键结论、待办事项和完整转写；`recap.json` 保存原始分段、时间、总结和 Agent provenance。`transcript.json` 在总结和渲染阶段保持不变。

## 可选 Word 输出

Word 是默认关闭的附加产物。未传入 `--word` 时，`render` 只生成 Markdown 和 `recap.json`，不会创建或修改 DOCX。明确需要 Word 时运行：

```bash
uv run recapit render \
  --transcript "outputs/湖滨路营业部 大会-<源摘要>/transcript.json" \
  --summary "outputs/湖滨路营业部 大会-<源摘要>/summary.json" \
  --word
```

若 Markdown 路径为 `outputs/湖滨路营业部 大会-<源摘要>/湖滨路营业部 大会.md`，配套 Word 路径固定为同目录的 `湖滨路营业部 大会.docx`。两者来自同一份最终 Markdown 内容；DOCX 保留标题、章节、元数据、项目符号、正文、强调、行内代码和时间码，并使用项目的 Python 依赖生成，不需要 Pandoc、LibreOffice 或 Microsoft Word。

## 时间码

时间码是可配置的，格式固定为 `[HH:MM:SS]`：

```bash
uv run recapit render --transcript ... --summary ... --timestamps paragraph
uv run recapit render --transcript ... --summary ... --timestamps segment
uv run recapit render --transcript ... --summary ... --timestamps none
```

默认按自然段显示时间码；`none` 只影响 Markdown，JSON 仍保留完整时间。

## 恢复、重启与覆盖

再次执行相同的 `transcribe` 命令会校验源内容和运行签名，然后自动恢复兼容的 chunk。模型、语言、chunk 或解码参数改变时，系统拒绝混用旧检查点。

只有明确需要忽略所有 chunk 并从头推理时才使用：

```bash
uv run recapit transcribe "records/录音.m4a" --restart
```

`--overwrite` 只属于 `render`，用于替换 `recap.json`、Markdown 和请求的可选 Word，不会修改 `transcript.json` 或 chunk 检查点。启用 Word 时，渲染会在写入前同时检查选定目标；明确允许替换派生产物时运行：

```bash
uv run recapit render \
  --transcript "outputs/录音-<源摘要>/transcript.json" \
  --summary "outputs/录音-<源摘要>/summary.json" \
  --word \
  --overwrite
```

DOCX 先在输出目录的临时文件中完整生成，再原子替换目标。若 Word 转换或替换失败，命令以非零状态退出，但会保留已经完成的 Markdown、`recap.json`、转写检查点和 `summary.json`。修复问题后可使用相同的 `render --word --overwrite` 命令重试，不会重新运行 Whisper 或 Agent 总结。

转写完成后即保留检查点，子 Agent 失败时可直接修正或重新生成 `summary.json`，无需再次运行 Whisper：

```bash
uv run recapit prepare-summary "outputs/录音-<源摘要>/transcript.json"
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

[transcription.chunking]
seconds = 900
overlap_seconds = 10

[transcription.decode]
beam_size = 5
vad_filter = true
hotwords = []

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
openspec validate optional-word-output --strict
openspec validate checkpointed-chunk-transcription --strict
```
