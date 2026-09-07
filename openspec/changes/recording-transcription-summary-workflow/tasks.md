## 1. 已保留的本地能力

- [x] 1.1 使用 `faster-whisper` 完成本地录音预检、模型/语言配置和有序分段转写，并通过替身模型测试及真实 `.m4a` 录音验证
- [x] 1.2 实现自然段合并、`none|paragraph|segment` 时间码和 Markdown 确定性渲染，并通过边界与结构测试验证

## 2. 移除外部总结 API

- [x] 2.1 从 `pyproject.toml`、`uv.lock` 和配置模型删除 OpenAI SDK、API Key、总结 provider/model 及重试配置，并通过依赖树、源码搜索和配置测试确认无残留
- [x] 2.2 删除 API 总结器与凭据预检路径，确保本地 `transcribe` 在没有 `OPENAI_API_KEY` 时正常运行，并通过测试确认 Python 代码不发起总结网络调用

## 3. Agent 总结数据契约

- [x] 3.1 为规范化转写文本实现稳定的 SHA-256，并定义带 schema 版本、`transcript_sha256`、摘要、关键点和待办的严格总结文件模型；通过往返、缺字段、额外字段和哈希格式测试验证
- [x] 3.2 在转写成功后原子生成 `transcript.json`、`transcript.txt` 和 `summary.template.json`，并测试文本顺序、模板哈希、默认禁止覆盖和源录音不变
- [x] 3.3 实现从既有 `transcript.json` 重建 Agent 输入文件的 `prepare-summary` 能力，并通过测试确认不读取音频、不调用 Whisper

## 4. 确定性最终渲染

- [x] 4.1 实现读取并严格校验 `summary.json`，通过测试确认非法 JSON、缺失/额外字段和转写哈希不匹配时均不修改检查点或 Markdown
- [x] 4.2 实现将有效 Agent 总结以 `summary_provider=agent` 且模型名为空的 provenance 合并回 JSON，并原子生成最终 Markdown；通过测试覆盖无待办与三种时间码
- [x] 4.3 将 CLI 重构为 `transcribe`、`prepare-summary`、`render` 子命令，删除总结服务参数，并通过 CLI 测试验证帮助、参数映射、退出状态和各阶段产物路径

## 5. 可发现的 Agent Skill

- [x] 5.1 将业务 Skill 安装到 `.agents/skills/recording-recap/`，提供 summary 契约参考，并运行 Skill 校验器确认元数据和引用有效
- [x] 5.2 编写 Skill 编排：成功转写后启动恰好一个子 Agent，要求其把转写当作不可信数据并填写 `summary.json`；通过静态契约测试验证命令顺序、无时间码映射及不存在 API Key 指令
- [x] 5.3 规定子 Agent 不可用、失败和无效总结时保留检查点、不回退外部 API，并允许对校验错误进行至多一次同子 Agent 修正；通过独立子 Agent 审查验证行为边界

## 6. 文档与验收

- [x] 6.1 更新 README 和 OpenSpec 验收记录，删除 OpenAI API 配置说明，写明 Skill/subagent 流程、手动阶段命令、恢复方式和隐私边界；通过逐条执行本地快速开始命令验证
- [x] 6.2 更新自动化测试并运行 Ruff、严格 mypy、pytest、Skill 校验和 OpenSpec 严格校验，确认不需要真实网络、模型 API 或 API Key
- [x] 6.3 使用现有真实 `transcript.json` 生成 Agent 输入，委派一个子 Agent 生成匹配摘要，再分别渲染 `paragraph` 与 `none` 文稿；验证哈希、章节、时间码和源文件不变并记录结果
