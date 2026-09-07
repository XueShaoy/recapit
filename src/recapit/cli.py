from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from recapit.config import load_config
from recapit.errors import RecapitError, WordExportError
from recapit.eta import estimate_transcription
from recapit.identity import resolve_device, run_signature
from recapit.performance import PerformanceHistory
from recapit.progress_display import ProgressRenderer
from recapit.transcribe import whisper_model_cached
from recapit.workflow import prepare_summary_inputs, render_recording, transcribe_recording

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="本地 Whisper 录音转写，以及供 Agent 使用的确定性产物工具。",
)


class TimestampOption(StrEnum):
    none = "none"
    paragraph = "paragraph"
    segment = "segment"


@app.command("transcribe")
def transcribe_command(
    recording: Annotated[Path, typer.Argument(help="要处理的本地录音文件。")],
    config_file: Annotated[Path, typer.Option("--config", help="项目配置文件。")] = Path(
        "recapit.toml"
    ),
    output_dir: Annotated[Path | None, typer.Option("--output-dir", help="产物根目录。")] = None,
    restart: Annotated[bool, typer.Option("--restart", help="忽略已有 chunk 并从头转写。")] = False,
    whisper_model: Annotated[
        str | None, typer.Option("--whisper-model", help="Whisper 模型规格。")
    ] = None,
    language: Annotated[
        str | None, typer.Option("--language", help="语言提示，例如 zh；auto 为自动检测。")
    ] = None,
    live_text: Annotated[
        bool,
        typer.Option("--live-text/--no-live-text", help="在进度中显示最新识别文字。"),
    ] = False,
) -> None:
    """只在本地转写录音，并生成 transcript.json/txt 和总结模板。"""
    renderer: ProgressRenderer | None = None
    try:
        config = load_config(
            config_file,
            overrides={
                "output_dir": output_dir,
                "whisper_model": whisper_model,
                "language": language,
            },
        )
        history = PerformanceHistory()
        actual_device = resolve_device(config.device)
        signature = run_signature(config, actual_device=actual_device)
        renderer = ProgressRenderer(
            model=config.whisper_model,
            device=config.device,
            compute_type=config.compute_type,
            estimate_for=lambda duration: estimate_transcription(
                duration_seconds=duration,
                model=config.whisper_model,
                device=config.device,
                history_rtfs=history.matching_rtfs(
                    model=config.whisper_model,
                    device=actual_device,
                    compute_type=config.compute_type,
                    run_signature=signature,
                ),
            ),
            model_cached=whisper_model_cached(config.whisper_model),
            live_text=live_text,
        )
        result = transcribe_recording(
            recording,
            config,
            progress=renderer.handle,
            performance_history=history,
            restart=restart,
        )
    except RecapitError as exc:
        if renderer is not None:
            renderer.close()
        typer.echo(f"错误：{exc}", err=True)
        raise typer.Exit(code=1) from exc
    except KeyboardInterrupt as exc:
        if renderer is not None:
            renderer.close()
        typer.echo("错误：转写已中断", err=True)
        raise typer.Exit(code=130) from exc
    renderer.close()
    typer.echo("✓ 本地转写完成")
    typer.echo(f"JSON: {result.paths.transcript_json.resolve()}")
    typer.echo(f"运行状态: {result.paths.run_manifest.resolve()}")
    typer.echo(f"文本: {result.paths.transcript_text.resolve()}")
    typer.echo(f"模板: {result.paths.summary_template.resolve()}")


@app.command("prepare-summary")
def prepare_summary_command(
    transcript: Annotated[Path, typer.Argument(help="已有的 transcript.json。")],
) -> None:
    """从既有转写检查点重建子 Agent 的文本输入和总结模板。"""
    try:
        paths = prepare_summary_inputs(
            transcript, progress=lambda message: typer.echo(f"→ {message}")
        )
    except RecapitError as exc:
        typer.echo(f"错误：{exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo("✓ Agent 输入已准备")
    typer.echo(f"文本: {paths.transcript_text.resolve()}")
    typer.echo(f"模板: {paths.summary_template.resolve()}")


@app.command("render")
def render_command(
    transcript: Annotated[Path, typer.Option("--transcript", help="transcript.json 路径。")],
    summary: Annotated[Path, typer.Option("--summary", help="Agent 生成的 summary.json。")],
    config_file: Annotated[Path, typer.Option("--config", help="项目配置文件。")] = Path(
        "recapit.toml"
    ),
    timestamps: Annotated[
        TimestampOption | None, typer.Option("--timestamps", help="Markdown 时间码粒度。")
    ] = None,
    overwrite: Annotated[
        bool | None, typer.Option("--overwrite/--no-overwrite", help="是否覆盖已有最终产物。")
    ] = None,
    word: Annotated[
        bool, typer.Option("--word/--no-word", help="是否额外生成同名 Word 文档。")
    ] = False,
) -> None:
    """校验 Agent 总结并确定性生成最终 Markdown 与 JSON。"""
    try:
        config = load_config(
            config_file,
            overrides={
                "timestamps": timestamps.value if timestamps else None,
                "overwrite": overwrite,
            },
        )
        result = render_recording(
            transcript,
            summary,
            config,
            word=word,
            progress=lambda message: typer.echo(f"→ {message}"),
        )
    except WordExportError as exc:
        typer.echo(f"错误：Word 导出失败：{exc}", err=True)
        typer.echo("已保留基础产物：", err=True)
        typer.echo(f"Markdown: {exc.markdown_path}", err=True)
        typer.echo(f"JSON: {exc.json_path}", err=True)
        raise typer.Exit(code=1) from exc
    except RecapitError as exc:
        typer.echo(f"错误：{exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo("✓ Markdown 渲染完成")
    typer.echo(f"Markdown: {result.markdown_path}")
    if result.word_path is not None:
        typer.echo(f"Word: {result.word_path}")
    typer.echo(f"JSON: {result.json_path}")


def main() -> None:
    app()
