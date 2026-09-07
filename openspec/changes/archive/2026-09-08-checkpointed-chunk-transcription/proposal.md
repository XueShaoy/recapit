## Why

当前长录音虽然能逐段显示进度，但 `faster-whisper` 仍会一次性解码整条录音，并且只有全部推理完成后才写入转写检查点。对于常见的 30–60 分钟录音，这会放大内存峰值，且在中断时丢失整次转写成果，因此需要建立有界内存、可恢复且产物状态明确的分块工作流。

## What Changes

- 将录音按 15 分钟核心区间规划为确定性 chunk，并使用边界上下文避免切断语句；每次只提取和处理一个临时音频块。
- 每完成一个 chunk 即原子保存检查点；重新执行时校验源文件与运行参数后复用已完成 chunk，最多重新处理当前 chunk。
- 顺序复用同一个 Whisper 模型，合并时恢复全局时间码、消除重叠区重复，并保持现有最终转写顺序。
- 引入权威运行 manifest，记录源文件指纹、运行参数、chunk 计划、阶段状态和已提交产物摘要。
- 将原始 `transcript.json` 设为不可变检查点，最终组合数据写入独立 `recap.json`；Markdown 和可选 Word 继续作为派生产物。
- 使用内容指纹生成不冲突的录音输出标识，严格校验当前唯一支持的 `faster-whisper` 引擎，并仅开放经过定义的 chunk、解码和 VAD 参数。
- 区分恢复、覆盖派生产物和从头重启三种操作语义，并使性能历史与总体进度适配分块执行。
- **BREAKING**：`transcribe --overwrite` 不再承担“重新转写”语义；默认执行兼容检查点恢复，显式从头处理改用 `--restart`，`--overwrite` 仅用于 `render` 的派生产物覆盖。

## Capabilities

### New Capabilities

- `checkpointed-chunk-transcription`: 定义 15 分钟分块、边界上下文、逐块检查点、恢复兼容性、全局时间码合并和总体进度行为。
- `artifact-lifecycle`: 定义不可变转写检查点、最终组合产物、manifest 提交顺序、阶段状态与部分失败恢复。
- `transcription-run-configuration`: 定义受支持引擎和参数校验、内容指纹输出标识、运行签名以及恢复、覆盖和重启语义。

### Modified Capabilities

无。本 change 以新增能力补充尚未归档的现有录音工作流规格。

## Impact

- 主要影响 `media.py`、`transcribe.py`、`workflow.py`、`artifacts.py`、`models.py`、`config.py`、`progress.py`、`performance.py` 和 CLI。
- 输出目录将增加 manifest、chunk 检查点和 `recap.json`，默认目录名称将包含源内容短哈希。
- 需要迁移 Skill 与 README，使其识别新检查点、恢复状态和最终 JSON 路径。
- 不增加远程服务或外部总结 API；音频 chunk 仍只在本地临时生成和处理。
