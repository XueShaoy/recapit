## Purpose

定义录音总结工作流如何在用户明确要求时，将最终 Markdown 内容额外导出为结构清晰、可编辑的 Word 文档，同时保持默认输出、覆盖保护和失败恢复行为稳定。

## ADDED Requirements

### Requirement: Word 输出必须显式启用
系统 SHALL 默认只生成现有 Markdown 和 JSON 产物；只有用户显式要求 Word 输出时，系统才 SHALL 额外生成 DOCX 文件。

#### Scenario: 使用默认输出
- **WHEN** 用户运行最终渲染且未启用 Word 输出
- **THEN** 系统生成 Markdown 和 JSON，且不创建 DOCX 文件

#### Scenario: 显式要求 Word 输出
- **WHEN** 用户在最终渲染中启用 Word 输出
- **THEN** 系统保留 Markdown 和 JSON，并在同一输出目录额外生成 Word 文档

#### Scenario: Skill 收到中文 Word 请求
- **WHEN** 用户通过 `recording-recap` Skill 明确要求“输出 Word”“生成 Word 文档”或“生成 docx”
- **THEN** Skill 启用 Word 输出选项，并在成功后返回 Markdown、Word 和 JSON 路径

### Requirement: Word 文档与 Markdown 内容一致
系统 SHALL 以最终 Markdown 为转换来源，将标题、处理元数据、AI 摘要、关键结论、待办事项和完整转写映射为对应的 Word 文档结构，不得改变正文语义或遗漏章节。

#### Scenario: 转换标准录音文稿
- **WHEN** 最终 Markdown 包含项目生成的标题、元数据、二级章节、列表和正文段落
- **THEN** Word 文档按相同顺序包含全部可见内容，并使用标题、列表和正文样式表达原结构

#### Scenario: 文稿包含时间码
- **WHEN** Markdown 转写段落包含 `[HH:MM:SS]` 时间码
- **THEN** Word 文档原样保留时间码及其对应文字

#### Scenario: 文稿不含待办事项
- **WHEN** Markdown 使用默认说明表示没有明确待办
- **THEN** Word 文档保留该说明且不生成虚构的待办条目

#### Scenario: 遇到不支持的 Markdown 结构
- **WHEN** 输入 Markdown 包含转换器无法无损表达的块级结构
- **THEN** Word 导出以非零状态失败并指出不支持的结构，不得静默丢弃对应内容

### Requirement: Word 产物具有稳定路径
系统 SHALL 使用与 Markdown 相同的文件名主体和输出目录，并将 Word 扩展名设为 `.docx`。

#### Scenario: 生成配套 Word 文件
- **WHEN** Markdown 路径为 `<输出目录>/<录音名>.md` 且 Word 输出启用
- **THEN** Word 路径为 `<输出目录>/<录音名>.docx`

### Requirement: Word 输出遵守覆盖和原子写入策略
启用 Word 输出时，系统 SHALL 在修改最终产物前同时检查 Markdown 和 DOCX 目标；未经明确允许不得覆盖任一已有文件，并且不得留下部分写入或损坏的 DOCX。

#### Scenario: Word 目标已经存在
- **WHEN** DOCX 目标已存在且用户未启用覆盖
- **THEN** 系统在修改 Markdown、最终 JSON 或 DOCX 前拒绝渲染，并提示使用覆盖选项或其他输出目录

#### Scenario: 用户允许覆盖
- **WHEN** Markdown 或 DOCX 已存在且用户显式启用覆盖
- **THEN** 系统使用原子替换更新所请求的最终产物

#### Scenario: DOCX 写入中途失败
- **WHEN** Word 内容已经生成但无法完成目标文件替换
- **THEN** 系统返回非零状态且不留下临时文件或破坏原有 DOCX

### Requirement: Word 失败不得破坏基础产物
Word 转换属于最终 Markdown 之后的附加导出步骤；转换失败时系统 MUST 保留有效的 Markdown、JSON、转写检查点和 Agent 总结，不得重新运行 Whisper 或总结子 Agent。

#### Scenario: Markdown 成功而 Word 转换失败
- **WHEN** Markdown 和最终 JSON 已成功写入，但 DOCX 转换发生错误
- **THEN** CLI 报告 Word 导出失败及已保留的基础产物路径，并以非零状态退出

#### Scenario: 修复后重试 Word 导出
- **WHEN** 用户在保留的检查点和总结文件上重新执行启用 Word 的最终渲染
- **THEN** 系统无需再次转写或总结即可重新生成 Markdown 和 DOCX

### Requirement: Word 生成不依赖外部办公软件
项目 SHALL 通过 `uv` 管理的本地 Python 依赖生成 DOCX，不要求用户安装 Pandoc、LibreOffice 或 Microsoft Word，也不向外部服务上传文稿。

#### Scenario: 没有安装办公软件
- **WHEN** 本机未安装 Pandoc、LibreOffice 和 Microsoft Word，但项目依赖已由 `uv` 同步
- **THEN** 系统仍能生成可被标准 Word 软件打开的 DOCX 文件
