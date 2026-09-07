## Context

参见 `proposal.md` 的动机。当前 `FasterWhisperTranscriber` 逐段消费第三方 generator，但第三方库会在推理前解码完整音频，工作流也要等全部分段进入单个 `Transcription` 后才落盘。现有进度、总结契约和可选 Word 导出已经按阶段解耦，因此新设计需要保留这些接口，同时把“转写完成检查点”细化为可恢复的 chunk 检查点。

现有已完成 change 尚未归档到主规格，本 change 使用三个不重名的新 capability 表达新增行为；实现时仍需保持旧 `transcript.json` 可由 `prepare-summary` 和 `render` 读取。

## Goals / Non-Goals

**Goals:**

- 将默认单次音频解码范围限制在 15 分钟核心区间及少量上下文。
- 让中断后的最大重复推理量不超过一个当前 chunk。
- 使 chunk 合并后的数据继续满足现有 `Transcription`、进度和总结哈希契约。
- 用 manifest 明确多文件提交状态，并让原始转写检查点不再被渲染阶段修改。
- 在不预先引入第二个引擎的情况下消除无效配置和同名输出冲突。

**Non-Goals:**

- 不提供目录批处理、多录音并发或同一录音多 chunk 并行推理。
- 不实现 chunk 级摘要、层级摘要、说话人分离或实时录音流。
- 不承诺任意故障点都能恢复当前 chunk 内部的 Whisper seek，只恢复已提交 chunk。
- 不在本 change 默认启用 batched inference；其速度、内存和边界准确率需另行基准验证。
- 不持久保存提取后的 PCM/WAV chunk。

## Decisions

### 1. 使用 900 秒核心区间和 10 秒双侧上下文

`ChunkPlan` 先按源时长生成连续核心区间 `[i*900, min((i+1)*900, duration))`，再为实际提取范围增加不越过源边界的 10 秒上下文。配置可以覆盖两个值，但默认值属于外部行为并写入 manifest。

纯粹无重叠硬切会在边界处截断语音；按 VAD 动态决定 chunk 边界则无法提前得到稳定计划，也会让恢复和总体进度难以复现。固定核心区间配合上下文可同时获得稳定身份与边界语境。

每次通过 ffmpeg 把一个提取范围转为临时单声道 16 kHz PCM WAV，再将该临时文件交给 faster-whisper。仅向原始文件传递 `clip_timestamps` 仍会触发整条音频解码，不能达到内存目标，因此不采用。

### 2. 顺序复用一个模型会话

把模型生命周期从“每次 `transcribe(path)` 创建模型”提升为一次 CLI 运行的 `TranscriptionSession`。会话延迟加载一个 Whisper 模型，并依次接收各临时 chunk；恢复运行时也只加载一次模型，然后处理所有缺失 chunk。

默认不并行处理 chunk，因为同时推理会增加模型设备内存、让日志与进度复杂化，并且 30–60 分钟录音通常只有 2–4 块。第一版也不把前一块文本作为下一块 `initial_prompt`，从而让每个 chunk 在相同源与配置下独立可重算；用户词汇提示可通过统一 `hotwords` 配置提供。

### 3. 以核心区间归属合并重叠分段

每个 chunk 文件保存提取起点、核心起止和转换后的绝对分段。合并器以分段时间中点落入哪个核心区间确定唯一归属，首块包含左端点，末块包含源末端；随后按 `(start, end, chunk_index)` 稳定排序并执行现有模型校验。

由于相邻 chunk 可能采用不同分段边界，合并器还要检查边界附近的时间倒退、超出容差的重叠和异常无覆盖区间。能够按确定性规则消解的重复直接处理；可能丢字的显著冲突停止合并并报告边界，而不是静默选择。边界容差应集中为内部常量并由合成样例和真实录音验收确定，不作为首版公共配置。

备选方案是开启 word timestamps 并按词裁剪，但会改变性能和现有 segment schema；模糊文本拼接又容易误删真实重复表达，因此均不作为默认方案。

### 4. chunk 检查点和 manifest 分层提交

建议目录布局：

```text
outputs/<sanitized-stem>-<source-sha256-12>/
|-- run.json
|-- chunks/
|   |-- 0000.json
|   |-- 0001.json
|   `-- ...
|-- transcript.json
|-- transcript.txt
|-- summary.template.json
|-- summary.json
|-- recap.json
|-- <stem>.md
`-- <stem>.docx
```

`run.json` 保存 schema version、完整源 SHA-256、当前展示路径、时长、运行签名、chunk 计划、每块状态和摘要、阶段及已提交最终产物摘要。更新顺序固定为“临时文件写完并 fsync -> 原子替换数据文件 -> 校验回读 -> 最后原子替换 manifest”。存在但未被最新 manifest 引用的文件视为未提交，不参与恢复。

同一录音目录在转写期间使用独占运行锁，避免两个 CLI 同时更新相同 manifest。锁只保护写入，读取和渲染仍通过 manifest 阶段与摘要判断可用性。异常遗留锁的处理需要验证进程身份后才允许显式恢复，不能仅按文件年龄删除。

### 5. 使用源身份与运行签名控制恢复

源身份由完整文件 SHA-256 和媒体时长组成，默认目录显示 stem 加摘要前 12 位；完整摘要负责发现短标识碰撞。运行签名由 engine、model、language、实际 device、compute type、chunk/overlap、VAD、beam size、hotwords 及其他影响结果的有效参数规范化后计算。

显示参数、Markdown 时间码和 Word 开关不进入转写签名。恢复要求源身份、计划和签名全部匹配；不匹配时不得在同一最终转写中混用检查点，而是提示用户沿用原配置或开始新运行。

默认 `transcribe` 恢复兼容运行；`transcribe --restart` 建立全新执行并忽略旧 chunk；`render --overwrite` 仅替换派生产物。旧 `transcribe --overwrite` 被移除，避免一个选项同时表达恢复、重推理和覆盖文档。

### 6. 将 transcript 与 recap 分离

合并完成后提交不可变 `transcript.json`，并由它生成 `transcript.txt` 和总结模板。Agent 继续只写 `summary.json`。渲染阶段验证文本哈希后生成包含总结 provenance 的 `recap.json`，Markdown 与 Word 均从同一已验证内存模型派生，不再回写 `transcript.json`。

最终产物逐个原子写入，manifest 最后提交所选目标的摘要。Word 失败可以保留已提交的 recap 和 Markdown，但 manifest 明确 Word 未完成且 CLI 返回非零。旧版、无 manifest 的有效 transcript 走兼容读取路径，可生成新 recap 和文档，但没有 chunk 恢复能力。

### 7. 聚合进度和性能历史使用唯一核心时长

总体进度等于已提交核心秒数加当前 chunk 映射到核心区间的有效进度，再除以源总时长；上下文秒数只显示在当前 chunk 细节中，不重复增加总百分比。恢复启动先发出已提交进度事件，随后保持单调。

模型加载一次，推理 RTF 使用全部 chunk 推理耗时除以唯一源时长，使上下文成本自然体现在结果中。历史 key 使用实际设备和规范化推理参数签名；与当前签名不匹配的样本只能作为保守首次估算，不能标记为精确同配置历史。

## Risks / Trade-offs

- [相邻 chunk 的 Whisper 分段边界可能不同，单纯按时间归属仍可能影响边界文字] -> 使用双侧上下文、确定性归属和显著冲突失败策略，并用跨边界语音样例验证。
- [每块都由 ffmpeg 启动一次，增加少量固定开销] -> 15 分钟粒度下启动次数有限，优先换取有界内存和恢复能力。
- [完整源文件 SHA-256 增加一次顺序读取] -> 哈希成本远低于本地 Whisper 推理，并换取稳定身份和安全恢复。
- [manifest 与数据文件无法形成真正的跨文件事务] -> 数据先提交、manifest 最后提交；消费者只信任 manifest 引用和摘要。
- [显式 `--restart` 可能留下不再引用的旧 chunk] -> 新执行使用新的运行标识或 staging 区；成功提交后再提供安全清理，不在开始时破坏可恢复数据。
- [旧版 transcript 没有源文件或运行签名完整信息] -> 仅允许继续总结和渲染，不声明具备 chunk 恢复能力。
- [输出目录加入摘要后改变现有脚本路径] -> CLI 始终打印解析后的绝对路径，README 和 Skill 同步迁移，旧 checkpoint 继续可读。

## Migration Plan

1. 增加版本化源身份、运行签名、chunk 计划、chunk 检查点和 manifest 数据模型，并保持旧 transcript loader。
2. 增加单 chunk ffmpeg 提取与清理组件，重构 faster-whisper 模型生命周期以支持会话内复用。
3. 实现逐块执行、原子 checkpoint、恢复校验、运行锁和全局时间轴合并。
4. 将总体进度与性能历史迁移到核心时长和有效运行签名。
5. 拆分不可变 `transcript.json` 与最终 `recap.json`，再调整 Markdown、Word 和 CLI 结果路径。
6. 引入严格 engine/config 校验、内容摘要输出身份以及 `--restart` 语义，移除 transcribe 的 `--overwrite`。
7. 更新 Skill、README、OpenSpec 验收记录，并以 30、45、60 分钟真实录音验证内存、恢复和边界质量。

回滚时保留新格式的 `transcript.json` 作为旧流程输入；忽略 `run.json`、`chunks/` 和 `recap.json` 即可继续使用总结与 Markdown 渲染。由于旧流程无法恢复 chunk，回滚后未完成运行只能从头转写。
