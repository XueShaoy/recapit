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

工作区现有 `records/fi交流.m4a` 经 `ffprobe` 确认为 3306.304 秒，可作为约 55 分钟样本。当前机器未发现已缓存的 Whisper 模型，且没有独立的 30、45、60 分钟跨边界语音样本，因此本次未执行耗时较长且可能触发模型下载的真实推理验收；对应 tasks 6.4 保持未完成。
