## 1. 配置、依赖与授权

- [x] 1.1 增加 `speakers` extra（pyannote.audio 及必要 PyTorch 栈），确认默认 `uv sync` 不安装该 extra，且 `uv sync --extra speakers` 能解析锁定依赖
- [x] 1.2 实现 Token 解析顺序 `HF_TOKEN` → `HUGGING_FACE_HUB_TOKEN` → `HUGGINGFACE_TOKEN`，用环境变量矩阵测试验证优先级，并断言日志与返回值不含 Token 明文
- [x] 1.3 增加 `speakers`/`max_speakers`/`num_speakers` 配置与 CLI `--speakers`/`--max-speakers`，默认关闭且上限为 4，用非法上限 `< 2` 的配置测试验证推理前失败
- [x] 1.4 在未安装 extra 时启用说话人，用导入失败测试验证非零退出并提示安装可选依赖，且不创建说话人检查点

## 2. 数据模型与词级检查点

- [x] 2.1 为 `Segment` 增加可选 `speaker` 字段，用含标签、缺字段和旧 JSON fixture 的 round-trip 测试验证旧转写仍可加载
- [x] 2.2 仅在启用说话人时让 Whisper 会话产出词级时间，并写入 chunk 检查点；用启用/关闭对比测试验证默认检查点不含词级数组
- [x] 2.3 定义说话人签名、`speakers.json` schema 与 `SPEAKER_XX` 到 `A`/`B`/… 的首次开口排序映射，用两人时间顺序和恢复后字母不变测试验证

## 3. 本机分离与对齐

- [x] 3.1 实现整段单声道 16 kHz 临时 WAV 提取、community-1 本地加载与 `exclusive_speaker_diarization` 调用替身接口，用命令参数和清理测试验证退出后不残留 WAV
- [x] 3.2 缓存命中时允许无 Token 离线加载；缓存未命中且无 Token 或 gated 401 时失败并点名环境变量/同意条款，用替身测试验证不会生成无标签成功文稿
- [x] 3.3 按词中点对齐独占说话人轴并切出说话人同质分段，用「单段 Whisper 跨越两人」合成样例验证最终至少两段且标签不同
- [x] 3.4 将 `max_speakers`/`num_speakers` 传入 pipeline，并用上限默认 4 与确切人数优先的单元测试验证调用参数

## 4. 分层恢复与工作流

- [x] 4.1 将分离标为独立阶段写入 `run.json`：未启用为 skipped，成功为 complete；先提交 `speakers.json` 再提交带标签的 `transcript.json`，用写入失败注入测试验证半文件不提交
- [x] 4.2 实现分层恢复：Whisper 签名匹配则复用 chunks；仅说话人签名变化则只重跑分离与对齐，用「改 max_speakers」测试验证不重新创建模型工厂/不重跑 chunk 推理
- [x] 4.3 未启用说话人时运行身份不因是否安装 extra 而变化，用签名对比测试验证；时间码/Word 开关仍不进入任一层签名

## 5. 段落、渲染与 Skill

- [x] 5.1 扩展 `merge_paragraphs`：换人强制切断，同一人仍按停顿和字数合并，用快速轮换与长独白样例测试验证
- [x] 5.2 扩展 `render_transcript`：`paragraph`/`segment` 输出 `[HH:MM:SS] A  正文`，`none` 输出 `A  正文`，无 speaker 时保持旧格式；用三种时间码模式测试验证
- [x] 5.3 用带说话人的 Markdown 生成 Word，验证 `.docx` 可见转写行前缀与 Markdown 一致且摘要契约测试仍通过
- [x] 5.4 更新 `recording-recap` Skill 与 README：可选 extra、Token 变量、community-1 同意页、CC-BY 署名、仅明确要求时传 `--speakers`；用 Skill 契约测试验证不上传音频、不做真名识别

## 6. 质量门禁

- [x] 6.1 运行 `ruff`、严格 `mypy` 和完整 `pytest`，确认默认测试不要求本机已安装 `speakers` extra
- [x] 6.2 运行 `openspec validate optional-speaker-diarization --strict`，确保 capability scenario 均有对应测试或明确的人工验收项
