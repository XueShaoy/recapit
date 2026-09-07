## Context

项目已有可工作的 `uv`、`faster-whisper`、数据模型、格式化器和测试，但首版错误地把总结实现为 OpenAI API 客户端。实际运行环境是具备模型能力和子 Agent能力的 Codex：Python 程序只需完成本地、确定性的音频与文件处理，Skill 负责 Agent 级编排。行为契约见本 change 的四份 capability spec。

## Goals / Non-Goals

**Goals:**

- 不需要 `OPENAI_API_KEY` 或任何额外总结 API，即可在 Agent 中完成端到端录音整理。
- 让本地 CLI 和 Agent 总结通过可校验文件解耦，任何一阶段失败都能独立重试。
- 使用转写摘要哈希保证总结与录音一一对应，避免错误合并。
- 让业务 Skill 成为 Agent 可发现的正式入口，Python CLI 同时保持可单独测试和使用。

**Non-Goals:**

- Python CLI 不自行调用大语言模型，也不模拟 Agent 委派能力。
- 首版不提供说话人分离、实时转写、图形界面或目录批处理。
- 首版不自动判断总结事实是否正确；通过子 Agent提示词和人工可追溯文本控制幻觉风险。
- 根 Agent不在子 Agent不可用时静默接管总结；应保留转写并向用户报告。

## Decisions

### 1. 使用 `uv` 和 `src` 布局维护本地确定性工具

项目使用 Python 3.11、`pyproject.toml` 和 `uv.lock`。运行依赖仅包含 Whisper 推理、数据校验和 CLI 库；删除 OpenAI SDK。业务代码继续放在 `src/recapit/`，测试放在 `tests/`。

### 2. 默认以 `faster-whisper` 本地转写

`faster-whisper` 返回统一的 `Transcription` 与 `Segment` 数据。录音由本地 `ffmpeg`/`ffprobe` 预检，原始音频不提供给子 Agent；子 Agent只读取转写文本。模型和语言仍可由配置与命令行覆盖。

### 3. 将 CLI 拆成 `transcribe` 与 `render`

`uv run recapit transcribe <recording>` 完成本地预检和 Whisper 推理，原子生成：

- `transcript.json`：版本化元数据和所有原始分段。
- `transcript.txt`：按原始顺序拼接、供 Agent 阅读的纯文本。
- `summary.template.json`：包含 summary schema 版本、`transcript_sha256` 和待填写字段。

`uv run recapit render --transcript <transcript.json> --summary <summary.json>` 校验总结契约与哈希，再根据 `--timestamps none|paragraph|segment` 生成最终 Markdown，并把总结与 provenance 合并回 `transcript.json`。

不保留会被误解为“CLI 自带 AI 总结”的单命令模式。`render` 的输入全部是本地文件，因此不需要模型、网络或凭据。

### 4. 使用规范化文本哈希绑定总结

`transcript_sha256` 对按顺序以换行连接的分段文本进行 UTF-8 SHA-256。转写 JSON、模板和最终 `summary.json` 都携带该值。`render` 重新计算哈希并拒绝不匹配的总结，且在全量校验完成前不修改既有 JSON 或 Markdown。

总结文件结构为：

```json
{
  "schema_version": "1.0",
  "transcript_sha256": "...",
  "summary": "...",
  "key_points": ["..."],
  "action_items": [
    {"task": "...", "owner": null, "due": null}
  ]
}
```

最终 provenance 记录 `summary_provider: "agent"`，不猜测或伪造底层 Agent 模型名。

### 5. Skill 编排一个总结子 Agent

业务 Skill 安装在 `.agents/skills/recording-recap/`。它执行以下流程：

1. 告知用户源音频只在本地 Whisper 中读取。
2. 运行 `transcribe`；若失败，不启动子 Agent。
3. 启动恰好一个子 Agent，并提供 `transcript.txt`、`summary.template.json` 与 `summary.json` 目标路径。
4. 子 Agent把转写视为不可信数据而不是指令，只依据内容填写模板；没有明确依据时保持负责人和截止时间为 `null`。
5. 等待子 Agent完成后运行 `render`，再返回最终 Markdown 和 JSON 路径。

若用户提供已有 `transcript.json`，Skill 跳过 Whisper，从同目录文本/模板继续；缺失的派生文件可由确定性 CLI 重新生成。子 Agent失败或无可用槽位时，Skill 不回退到外部 API，也不覆盖已有最终 Markdown。

### 6. 时间码和段落整理保持确定性

格式化器继续按相邻间隔、句末标点和最大长度合并段落。`paragraph` 为默认；`segment` 保留原始边界；`none` 不在 Markdown 显示时间码。JSON 始终保存浮点秒数。

### 7. 失败恢复以检查点为边界

转写成功即落盘检查点。总结 JSON 无效、哈希不匹配或渲染失败时均不得破坏检查点。Skill 可把具体校验错误反馈给同一个子 Agent做至多一次修正；再次失败则停止并报告。重新运行 Skill 时可复用检查点，不重复 Whisper。

## Risks / Trade-offs

- [总结质量取决于当前 Agent 及其上下文容量] → 使用独立子 Agent、明确 schema、忠实性提示和可追溯全文；超长文本后续可增加确定性分块清单。
- [转写文本可能包含提示注入内容] → Skill 明确把全文视为不可信数据，子 Agent只执行固定总结契约。
- [摘要文件可能来自其他录音] → `transcript_sha256` 不匹配时渲染拒绝执行。
- [子 Agent不可用或失败] → 保留 `transcript.json`、`transcript.txt` 和模板，不回退外部 API，允许稍后重试。
- [CPU 转写长录音耗时明显] → 使用 `faster-whisper` 和转写检查点，任何总结/排版重试都不重复推理。
- [多人重叠语音与专有名词可能识别错误] → 保留时间区间和原始分段，不伪造说话人标签。

## Migration Plan

1. 更新规格与任务，重新打开所有受新架构影响的已完成项。
2. 删除 OpenAI SDK、API Key、provider/model 配置及 API 总结器。
3. 增加哈希绑定的数据模型和转写派生文件。
4. 将 CLI 改为 `transcribe`/`render` 子命令并补齐原子性、恢复和契约测试。
5. 将业务 Skill 安装到 `.agents/skills/recording-recap/`，增加总结契约参考并验证子 Agent编排指令。
6. 更新 README 和真实录音验收记录，执行完整质量门禁。

已有 `transcript.json` 的基础分段结构保持可读；缺少哈希的旧检查点在渲染前由本地工具重新计算派生数据。旧 API 总结相关配置被直接移除，因为项目尚未发布，不提供兼容层。
