## Why

当前转写只输出「说了什么」，Markdown 按停顿和字数换段，多人会议会被合成一段连续独白。用户需要匿名说话人标签（人物 A/B/C/D）和「换人即换段」，且必须在本机完成、不上传录音。

## What Changes

- 增加可选的本机说话人分离；默认关闭，不影响现有转写、总结和渲染。
- 使用本地开源分离 pipeline（pyannote community-1），第一次下载 gated 权重时读取已有 Hugging Face Token；之后离线运行。不调用 pyannoteAI 云端商业模型，不把音频发到第三方。
- Token 解析顺序覆盖用户现有环境：`HF_TOKEN`、`HUGGING_FACE_HUB_TOKEN`、`HUGGINGFACE_TOKEN`。不把 token 写入配置文件或 git。
- 说话人标签为 `A`/`B`/`C`…，可配置人数上限，默认 `max_speakers = 4`。不对真实姓名做识别或映射。
- 将说话人写入转写分段；段落合并在现有停顿/字数规则之外，遇换人强制新开段。
- Markdown 与可选 Word 在时间码后标注说话人，例如 `[00:01:12] A  正文`。未启用分离或旧检查点无说话人字段时，渲染与现在一致。
- 分离作为独立、可恢复步骤纳入运行签名与检查点；未安装可选依赖或缺少授权时给出明确错误，而不是默认静默省略标签。

## Capabilities

### New Capabilities

- `speaker-diarization-run`: 定义可选启用、本机 pipeline、Token 与 gated 模型授权、人数上限、检查点/运行签名以及失败诊断。
- `speaker-labeled-transcript`: 定义匿名标签、分段 schema、换人换段，以及 Markdown/Word 时间码行格式。

### Modified Capabilities

无。本 change 以新增能力补充尚未归档到 `openspec/specs/` 的录音工作流规格。

## Impact

- 主要影响 `models.py`、`config.py`、`cli.py`、`workflow.py`、`formatter.py`、`artifacts.py`/`run_state.py` 以及转写会话；新增分离模块与可选依赖 extra。
- 项目将增加 PyTorch / pyannote.audio 一类重量级依赖，但必须保持默认安装路径不强制拉取。
- 运行签名在启用分离时纳入模型、人数上限等影响结果的参数；显示用时间码模式仍不进入转写签名。
- 更新 README、`recording-recap` Skill（当前写明不适用说话人识别）和相关测试。
- 与进行中的分块转写并存：Whisper 继续按 chunk 推理；说话人轴在整段源音频上保持全局一致，避免 15 分钟边界跳号。
