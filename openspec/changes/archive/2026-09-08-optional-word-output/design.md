## Context

最终文稿目前由 `render_markdown` 生成受控结构的 UTF-8 Markdown，并通过 `render_recording` 写入 `<录音名>.md`。`ArtifactPaths` 和 `RenderResult` 只包含 Markdown 与 JSON，CLI `render` 没有附加格式选项。

当前机器没有 Pandoc 或 LibreOffice，项目虚拟环境也未安装 DOCX 库。`markdown-it-py` 目前由其他包间接引入，但不能把间接依赖视为稳定的项目接口。

## Goals / Non-Goals

**Goals:**

- 在不改变默认输出的前提下提供显式、确定的 Word 附加导出。
- 保证 Word 的章节顺序和可见文字与最终 Markdown 一致。
- 为中文标题、列表和正文提供可读的基础 Word 样式。
- 复用现有检查点，使 Word 导出失败后不需要重新转写或总结。

**Non-Goals:**

- 不把 Word 设为默认或唯一产物。
- 不接受任意第三方 Markdown 方言，也不承诺转换表格、图片、HTML、脚注或复杂嵌套布局。
- 不实现 DOCX 模板上传、企业品牌样式、页眉页脚、目录或 PDF 导出。
- 不依赖 Agent 在运行时调用文档工具完成业务转换。

## Decisions

### 1. 使用 `--word` 生成附加产物

`render` 增加 `--word/--no-word` 布尔选项，内置默认值为关闭。启用时仍先生成标准 Markdown，再生成同名 `.docx`。`recording-recap` Skill 只在用户明确提到 Word/docx 时传入 `--word`。

相比 `--format markdown|docx|both`，布尔开关不会产生“只生成 Word、是否还保留稳定 Markdown”的歧义，也符合 Word 是派生产物的定位。

### 2. 使用 `python-docx` 与 `markdown-it-py`

项目将 `python-docx` 和 `markdown-it-py` 声明为直接运行时依赖，并由 `uv.lock` 固定。转换器解析 Markdown token，而不是用正则表达式猜测结构：

- 一级标题映射为 Word 文档标题；
- 二级标题映射为一级章节标题；
- 无序列表映射为项目符号；
- 普通段落映射为正文；
- 行内代码移除 Markdown 定界符并使用等宽字符样式；
- 加粗等受控行内标记映射为对应 run 属性；
- 时间码作为普通可见文字原样保留。

转换器仅支持项目自身渲染器会产生的 Markdown 子集。遇到未支持且可能造成内容丢失的块级 token 时显式失败。

备选方案 Pandoc 转换能力更全面，但会增加系统级二进制依赖和安装差异；调用 Agent 文档 Skill 则无法保证普通 CLI 环境可用，因此均不作为运行时方案。

### 3. Word 样式保持简洁和跨客户端兼容

文档采用 A4 页面、常规页边距、标题层级、项目符号和适度段后间距。中文正文和标题设置东亚字体提示，同时保留 Word 主题的西文字体回退。样式集中配置，不在业务内容转换中散布格式数值。

该 change 不追求复杂视觉设计；视觉验收重点是内容不截断、层级清楚、中文正常显示、列表和长转写可阅读。

### 4. 从最终 Markdown 内容转换

Word 转换器接收与写入 `.md` 完全相同的 Markdown 字符串以及目标路径，确保两个可见产物来自同一内容源。转换不直接读取 `Transcription` 或重新实现摘要、时间码和段落规则。

相比直接从结构化模型分别渲染 DOCX，这一选择减少两个渲染器内容逐渐不一致的风险，也满足“将 `.md` 转为 Word”的产品语义。

### 5. 对选定目标执行预检和分阶段提交

`ArtifactPaths` 增加稳定的 `.docx` 路径，`RenderResult` 增加可空的 Word 路径。启用 Word 时，工作流在任何最终产物写入前检查 Markdown 和 DOCX 是否存在；未启用覆盖时任一冲突都立即失败。

成功路径为：验证输入、生成 Markdown 字符串、写入最终 JSON、原子写入 Markdown、在临时文件中生成 DOCX、原子替换 DOCX。若最后一步失败，已经有效写入的 Markdown 和 JSON 保留，CLI 把 Word 视为用户请求中未完成的必需产物并返回非零状态。

整个 DOCX 包必须先在目标目录的临时文件中完成并关闭，随后使用原子替换，避免跨文件系统移动及半写文件。

### 6. Word 不进入转写阶段的目标冲突集合

`transcribe` 不生成 Word，也不应仅因旧 `.docx` 存在而拒绝创建新的转写检查点。DOCX 目标只在启用 Word 的 `render` 阶段参与冲突检查。这样保持转写阶段与可选派生格式解耦。

## Risks / Trade-offs

- [Markdown 与 DOCX 的格式能力不同] → 明确定义受支持子集，对可能丢失内容的未知块结构显式失败。
- [DOCX 是 ZIP 包，直接写目标文件可能损坏] → 先保存到同目录临时文件，关闭后原子替换。
- [Word 失败发生在 Markdown 成功之后] → 报告部分成功和保留路径，返回非零状态并允许从检查点重试。
- [中文字体在不同系统名称不一致] → 设置常见东亚字体提示并保留主题回退，不嵌入字体文件。
- [间接 Markdown 解析依赖未来消失] → 将解析器声明为直接依赖并锁定。
- [转换器范围不断膨胀] → 仅承诺项目渲染器当前产生的 Markdown，不在本 change 支持任意 Markdown。

## Migration Plan

1. 增加并锁定 Markdown 解析与 DOCX 生成依赖。
2. 扩展产物路径和渲染结果，默认 Word 路径保持未请求状态。
3. 实现受控 Markdown 到 DOCX 转换器及原子保存。
4. 为 `render` 增加 `--word`，完善目标预检、错误报告和成功路径输出。
5. 更新 `recording-recap` Skill 和 README。
6. 使用固定 Markdown 样例做结构测试，并使用真实录音结果生成 Word 后进行解包检查和页面渲染视觉验收。

回滚时可移除 `--word`、转换模块和新增依赖；现有 Markdown、JSON 和检查点格式均未改变，无数据迁移。
