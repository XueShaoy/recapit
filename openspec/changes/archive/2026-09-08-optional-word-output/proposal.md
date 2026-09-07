## Why

当前工作流只生成 Markdown 和 JSON，不便于需要使用 Microsoft Word 阅读、批注、打印或继续编辑的中文用户。Word 应作为用户明确要求时才生成的附加产物，不能改变现有默认输出和转写总结流程。

## What Changes

- 为 `render` 增加显式 Word 输出选项；默认关闭，开启时在保留 Markdown 的同时生成同名 `.docx`。
- 将项目生成的 Markdown 标题、元数据、摘要、列表、待办和转写段落映射为结构清晰的 Word 文档。
- 使用项目内 Python 依赖完成转换，不要求用户另行安装 Pandoc、LibreOffice 或 Microsoft Word。
- 将 `.docx` 纳入覆盖检查和原子写入策略；Word 生成失败时保留已完成的 Markdown、JSON 和转写检查点，并返回失败状态。
- 更新 `recording-recap` Skill，使其仅在用户明确要求 Word/docx 时传递对应选项并返回 Word 文件路径。

## Capabilities

### New Capabilities

- `word-document-output`: 定义可选 Word 输出、Markdown 到 DOCX 的内容映射、文件命名、默认行为、覆盖保护和失败恢复。

### Modified Capabilities

无。

## Impact

- 影响 `src/recapit/artifacts.py`、`src/recapit/workflow.py` 和 `src/recapit/cli.py` 的最终产物路径、渲染结果和选项。
- 新增本地 Markdown 到 DOCX 转换模块，并在 `uv` 项目依赖中直接声明 Markdown 解析器和 DOCX 生成库。
- 更新 README、`recording-recap` Skill 和相关测试；不改变 Whisper、Agent 总结、JSON schema 或默认 Markdown 内容。
