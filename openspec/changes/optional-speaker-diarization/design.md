## Context

参见 `proposal.md` 的动机。当前 `Segment` 只有 `start`/`end`/`text`；`merge_paragraphs` 按停顿、句末和最大字数换段；`faster-whisper` 按 chunk 推理且不分说话人。`recording-recap` Skill 与既有 change 均把说话人分离列为非目标。

分块转写（`checkpointed-chunk-transcription`）把单次 Whisper 解码限制在约 15 分钟核心区间。说话人标签必须跨 chunk 全局稳定，因此不能对每块独立编号。项目已有 `HUGGINGFACE_HUB_CACHE` 解析，但官方 Hub 客户端不读取用户现用的 `HUGGINGFACE_TOKEN`。

## Goals / Non-Goals

**Goals:**

- 把说话人分离做成默认关闭的可选流水线，失败时显式退出。
- 用词级时间与独占说话人轴对齐，满足「换人必换段」。
- 让 Whisper chunk 检查点在只增加分离时仍可复用。
- Token 解析覆盖 `HUGGINGFACE_TOKEN`，且永不写入仓库或日志。

**Non-Goals:**

- 不识别真实姓名，不维护声纹花名册。
- 不调用 pyannoteAI Precision 等云端商业模型。
- 不把叠音拆成并行两行，不保证远场/多人嘈杂场景的 DER。
- 不把 PyTorch/pyannote 打进默认 `uv sync` 依赖。
- 不修改摘要 JSON 字段；不在本 change 重做 chunk 时长策略。

## Decisions

### 1. 可选 extra 安装 pyannote.audio，默认 CLI 不导入

在 `pyproject.toml` 增加 extra（名称建议 `speakers`），包含 `pyannote.audio` 及其 PyTorch 栈。默认依赖保持 faster-whisper。启用 `--speakers` 时再导入分离模块；缺依赖时抛出可操作的配置/转写错误。

备选是把 torch 设为默认依赖，会显著拖慢未使用该功能的安装，否定「默认路径不变」。

### 2. 使用本机 `pyannote/speaker-diarization-community-1`

分离模型固定为 community-1（CC-BY-4.0，gated，可离线缓存）。README 与错误文案提示在同一 HF 账号上同意该模型条款。禁止默认走 Precision-2 云端 pipeline。

备选 WhisperX 会再引入对齐模型和更重的版本钉扎，且与现有 faster-whisper 会话重复。本 change 只加分离，ASR 仍用现有引擎。

推理输入：用现有 ffmpeg 抽一条临时单声道 16 kHz WAV（可整段，不按 15 分钟切），交给 pipeline 后删除。避免对原始 m4a 做不可控的库内解码，也避免把说话人编号限制在单个 Whisper chunk 内。

人数：配置 `max_speakers`（默认 4），映射到 pipeline 的 `max_speakers`；若用户给出确切 `num_speakers` 则优先使用确切值。

设备：优先使用已解析的 Whisper 设备语义中可映射到 torch 的部分（CUDA/MPS），否则 CPU。不为此单独引入新的「第二套 auto」配置面。

### 3. Recapit 显式解析 Token 并传入加载接口

解析顺序：`HF_TOKEN` → `HUGGING_FACE_HUB_TOKEN` → `HUGGINGFACE_TOKEN`。将得到的值传给 `Pipeline.from_pretrained(..., token=...)`。权重已在 Hub 缓存且可离线加载时，不得因环境无 Token 而失败。

日志、`run.json`、异常消息只允许出现变量名，不允许出现 Token 值。

### 4. 启用分离时打开词级时间戳，再用独占说话人轴切段

仅启用说话人时，Whisper 会话打开 word timestamps，并在 chunk 检查点保存词级 `start`/`end`/`text`（未启用时不保存，以免扩大默认检查点）。全部分块合并后：

1. 运行分离，读取 `exclusive_speaker_diarization`（每一时刻只归属一人，便于对齐 ASR）。
2. 按词中点落入的说话人区间打标签。
3. 相邻且同一说话人的词重组成最终 `Segment`，再交给现有 `merge_paragraphs`，并增加「说话人变化 => 强制切断」。

字母标签：将 pipeline 的 `SPEAKER_XX` 按该说话人最早起始时间排序，映射为 `A`、`B`、`C`…。映射表写入说话人检查点，保证恢复后字母不变。

若引擎未返回词级时间（测试替身或旧检查点），允许按分段与说话人区间重叠做近似切分，但生产路径以词级为准。

### 5. 说话人检查点与 Whisper 运行签名分层

Whisper 运行签名保持「影响 ASR 的参数」，显示参数仍排除在外。另计算说话人签名：pipeline 标识、人数约束、对齐规则版本。`run.json` 增加分离阶段：`pending` / `complete` / `skipped`（默认关闭时为 skipped）。

产物建议：

```text
outputs/<stem>-<source-sha12>/
|-- run.json
|-- chunks/
|-- speakers.json          # 说话人轴、字母映射、签名
|-- transcript.json        # 最终分段含 speaker
`-- ...
```

`speakers.json` 先于带标签的 `transcript.json` 提交。恢复时：Whisper 签名匹配则复用 chunks；说话人签名匹配则复用 `speakers.json` 并跳过推理；只改 `max_speakers` 时重跑分离与对齐，不重跑 Whisper。

旧的无 `speaker` 字段分段继续合法。启用分离成功后每个最终分段必须有标签。

### 6. 渲染从结构化模型生成说话人前缀，Word 仍吃 Markdown

`render_transcript` 在有说话人时输出约定前缀；`timestamps none` 仍保留字母。Word 继续从最终 Markdown 转换，因此自动获得相同可见文字，无需第二套说话人排版。

Agent 总结仍吃纯文本/现有模板；不把 A/B 写入 `summary.json` 契约。

### 7. CLI 与配置面

- `--speakers/--no-speakers`，配置项默认 `false`。
- `--max-speakers`，默认 4，校验 `>= 2`。
- 可选 `--num-speakers`（确切人数）；与上限同时给出时以确切人数为准。

Skill：仅当用户明确要求说话人/人物 A/B/谁在说时传 `--speakers`。更新 Skill 描述，删除「不适用于说话人识别」中与本功能冲突的部分，但仍声明不做真名识别。

README 注明 CC-BY-4.0 署名、可选 extra 安装、Token 变量名和模型同意页。

## Risks / Trade-offs

- [词级时间戳增加 Whisper 耗时和 chunk JSON 体积] → 仅在启用说话人时打开；默认路径不变。
- [整段 16 kHz WAV 与「有界解码」目标部分冲突] → 该文件约 2 MB/分钟，远小于 Whisper 整段解码；用完即删，且不替代 chunk 转写。
- [PyTorch 与 CTranslate2 同机争 GPU] → 默认顺序执行：先 Whisper 会话结束或释放后再跑分离；内存不足时回退 CPU 并记录实际设备。
- [分离错误率在插话、远近麦、音色接近时升高] → 规格只保证标签格式与换段行为，不承诺 DER；人数上限降低乱裂。
- [用户只设 `HUGGINGFACE_TOKEN` 而库不识别] → Recapit 显式三联解析并在缺 Token 时打印变量名。
- [启用分离后若静默降级会生成「看起来成功」的无标签文稿] → 启用则必须成功打标签，否则非零退出并保留 Whisper 检查点。
- [community-1 未来改条款或改名] → 运行身份写入模型 id；下载失败指向当前模型页，不自动改用云端 Precision。

## Migration Plan

1. 增加 Token 解析、可选 extra、配置/CLI 开关与非法人数校验。
2. 扩展分段与 chunk 检查点，支持可选 `speaker` 和启用时的词级时间。
3. 实现本机分离、独占轴对齐、字母映射、`speakers.json` 与分层恢复。
4. 调整段落合并和 Markdown 渲染；用现有 Word 转换保证 `.docx` 一致。
5. 更新 Skill、README 与测试（含无 Token 失败、旧 transcript 兼容、换人切断）。

回滚：不安装 extra、不传 `--speakers` 即回到当前行为。已写入的 `speaker` 字段对旧渲染器应忽略或视为未知则需保证 pydantic 兼容（未知字段策略保持现有 frozen 模型；新增可选字段即可）。

## Open Questions

无。设备回退（MPS/CPU）的具体选择可在实现时按 torch 可用性决定，不改变规格中的本机、可选、显式失败行为。
