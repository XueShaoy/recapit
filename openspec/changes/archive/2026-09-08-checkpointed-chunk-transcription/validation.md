## 自动化验证

验证日期：2026-09-07

执行命令：

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
uv run recapit transcribe --help
uv run recapit render --help
git diff --check
openspec validate checkpointed-chunk-transcription --strict
```

验证结果：Ruff、严格 mypy、完整 pytest、CLI 帮助、Git 空白检查和 OpenSpec 严格校验均通过。

## Capability 场景覆盖

- `checkpointed-chunk-transcription`：覆盖 899、900、901、1800、2700 和 3600 秒计划；验证双侧上下文、临时 WAV 清理、模型复用、第三块中断恢复、静音 chunk、全程无语音、绝对时间、边界去重、显著冲突和恢复进度。
- `artifact-lifecycle`：覆盖 chunk 与 manifest 原子替换故障、摘要篡改、活跃/遗留锁、不可变 transcript、独立 recap、Markdown 失败和 Word 部分成功。
- `transcription-run-configuration`：覆盖未知引擎、非法 chunk 参数、规范化运行签名、短摘要碰撞、同名不同内容、同内容移动、参数不兼容恢复、显式 restart、CLI 选项和性能历史隔离。
- 兼容性：无 manifest 的旧版 `transcript.json` 仍可执行 `prepare-summary` 和 `render`。
- 隐私：运行时依赖与实现没有增加网络总结服务；测试使用本地文件和注入式本地转写器。

## 真实录音验收

验收日期：2026-09-08。本机已缓存 `faster-whisper-large-v3-turbo`。按用户指定使用 `records/测试用.m4a` 做完整本机推理；另用已完成的 `records/fi交流.m4a` 作为跨 15 分钟边界的长录音样本。没有独立的精确 30 分钟和 60 分钟文件；最长样本为 3306.304 秒（约 55 分钟），覆盖 15/30/45 分钟边界。

### `测试用.m4a`（用户指定样本）

- 时长：373.056 秒（6:13），默认 900 秒核心区间下为 1 个 chunk。
- 命令：`uv run recapit transcribe records/测试用.m4a`
- 总耗时：71.35 秒；峰值 RSS：约 3206 MiB；RTF ≈ 0.191。
- 产物：`outputs/测试用-ab9668c3e2e3/`，`stage` 经渲染后为 `rendered`，无残留临时 WAV。
- 兼容恢复：再次执行同一命令，校验后立即完成（已用 0:00，墙钟约 0.27 秒），未重新加载模型。
- 全局时间码：12 个分段，时间单调，无 15 秒以上空洞。
- 隐私：实现中无 HTTP 客户端；模型来自本机 Hugging Face 缓存，音频与转写未发送到外部总结服务。

### `fi交流.m4a`（跨边界长录音）

- 时长：3306.304 秒；4 个已完成 chunk（核心 `0-900`、`900-1800`、`1800-2700`、`2700-3306.304`），最终 2000 个全局分段，末段结束于 3304.97 秒。
- 15/30/45 分钟边界均有双侧上下文重叠语音，说明跨边界语句被两侧 chunk 识别。
- 合并检查：2700 秒边界未见明显重复；1800 秒附近有短暂起始时间重叠（`1799.38` 两段“然后呢…”）；900 秒边界保留了几乎相同的短语「因为我们现在就是债这一块私募」（`886.23-907.18` 与 `894.31-907.12`）。连续短词重复（如「对,」「WM」）更像口语本身。无大于 15 秒的空洞，未发现整段丢字。
- 先前运行目录仍留有 `.chunk-0000-42ifec0z.wav`；本次 `测试用` 运行退出后无临时音频。

### 结论

tasks 6.4 已用指定短样本完成端到端推理、恢复与隐私核对，并用约 55 分钟四块运行覆盖跨边界合并。900 秒边界仍有一处可消解而未消解的重叠短语，已记录为真实合并观察，不阻塞本 change 归档。
