"""Command line entry point: `nightcut run recording.mkv --profile bf6`."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer

from .config import PROFILES_DIR, load_config

app = typer.Typer(add_completion=False, help="Overnight local-AI editing pipeline for gameplay recordings.")


@app.command()
def run(
    recordings: list[Path] = typer.Argument(..., exists=True, dir_okay=False,
                                            help="One or more recordings (mkv/mp4), in playing order."),
    mode: Optional[str] = typer.Option(None, "--mode", "-m", help="highlights or letsplay (overrides config)."),
    strength: Optional[str] = typer.Option(None, help="Let's Play cut strength: gentle, normal or tight."),
    name: Optional[str] = typer.Option(None, help="Name of the output folder (default: name of the recording)."),
    profile: str = typer.Option("bf6", "--profile", "-p", help="Game profile name or YAML path."),
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Your config overrides (YAML)."),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output folder (overrides config)."),
    force: list[str] = typer.Option([], "--force", "-f", help="Re-run a stage even if cached (repeatable)."),
    no_vision: bool = typer.Option(False, help="Skip the vision model."),
    no_llm: bool = typer.Option(False, help="Pick clips by score only, without the LLM."),
    fill: Optional[bool] = typer.Option(None, "--fill/--no-fill",
                                        help="Highlights: fill a calm session up to the target length with quiet "
                                             "moments, or rather make a shorter video. Asked at start if not given."),
    gpus: Optional[str] = typer.Option(None, "--gpus",
                                       help="Highlights: how many NVIDIA GPUs to use, e.g. 1 or 2 "
                                            "(default: gpus.use in the config, 'auto' = all cards)."),
):
    """Analyse recordings and write editor-ready timelines."""
    from .letsplay import LetsPlayRun
    from .pipeline import Run

    cfg = load_config(config, profile)
    if mode:
        cfg["mode"] = mode
    if strength:
        cfg["letsplay"]["strength"] = strength
    if output:
        cfg["output_dir"] = str(output)
    if no_vision:
        cfg["vision"]["enabled"] = False
    if no_llm:
        cfg["editor"]["enabled"] = False
    if cfg["mode"] == "letsplay":
        xmls = LetsPlayRun(recordings, cfg, set(force), name).run()
        typer.echo("\nTimelines:\n" + "\n".join(f"  {x}" for x in xmls) + f"\nReport: {xmls[0].parent / 'report.md'}")
        return
    if fill is not None:
        cfg["editor"]["fill"] = fill
    elif cfg["editor"].get("fill") is None and cfg["editor"].get("enabled", True):
        if sys.stdin.isatty():  # ask now, not in the middle of the night
            cfg["editor"]["fill"] = typer.confirm(
                "Calm session: fill up to the target length with quiet moments? (no = shorter video)", default=True)
        else:
            cfg["editor"]["fill"] = True
            typer.echo("Not asked (no terminal): filling up to the target length. Use --fill/--no-fill.")
    for recording in recordings:  # highlights: one video per recording
        folder = None if not name else name if len(recordings) == 1 else f"{name}_{recording.stem}"
        xml = Run(recording, cfg, set(force), folder, use_gpus=gpus).run()
        typer.echo(f"\nTimeline: {xml}\nReport:   {xml.parent / 'report.md'}")


@app.command()
def summary(
    folders: list[Path] = typer.Argument(..., exists=True, file_okay=False, help="Output folders of finished runs."),
):
    """Print the short morning message for finished runs (for Hermes, chat or phone)."""
    import json

    from .summary import text

    for folder in folders:
        path = folder / "summary.json"
        if not path.exists():
            typer.echo(f"⚠️ {folder.name}: no summary.json (run not finished?)")
            continue
        typer.echo(text(json.loads(path.read_text(encoding="utf-8"))) + "\n")


@app.command()
def layout(
    recording: Path = typer.Argument(..., exists=True, dir_okay=False, help="A recording (mkv/mp4)."),
    save: Optional[Path] = typer.Option(None, "--save", "-s",
                                        help="Write the result into this config, e.g. config/local.yaml "
                                             "(only its layout section is replaced)."),
    frames: int = typer.Option(8, help="How many frames to check."),
):
    """Find where gameplay and facecam sit in a recording and print the layout for your config."""
    from .layout import detect, draw_preview, save_layout, to_yaml

    cfg = load_config(save if save and save.exists() else None)
    result = detect(recording, cfg, frames, log=typer.echo)
    preview = draw_preview(recording, result, Path.cwd() / f"layout_{recording.stem}.png")
    block = to_yaml(result, recording.name)
    note = {"canvas": "Gameplay and facecam are separate areas: nightcut cuts them into separate files.",
            "burned_in": "Facecam is part of the gameplay picture (burned in).",
            "no_cam": "No facecam found: gameplay is the whole picture."}[result["kind"]]
    typer.echo(f"\n{note}\n\n{block}\nCheck the preview: {preview}")
    if save:
        save_layout(save, block)
        typer.echo(f"Saved to {save}. Use it with: nightcut run ... --config {save}")
    else:
        typer.echo("Copy the block into your config (e.g. config/local.yaml) or run again with --save config/local.yaml")


@app.command()
def tracks(
    recording: Path = typer.Argument(..., exists=True, dir_okay=False, help="A recording (mkv/mp4)."),
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Your config overrides (YAML)."),
):
    """Show the audio tracks and tell nightcut which one is your mic (listen to short pieces).
    The answer is remembered for all recordings with the same tracks."""
    from . import tracks as tr
    from .media import probe

    cfg = load_config(config)
    info = probe(recording)
    sample = tr.measure(info, info.audio)
    typer.echo(f"{recording.name}: {len(info.audio)} audio track(s)")
    for s in info.audio:
        lv = sample.levels[s.index]
        kind = tr.kind_by_name(s.title) or "-"
        typer.echo(f"  track {s.position + 1}: {s.title[:30]:30s} name says: {kind:5s}  sound {lv.sound:4.0%}  "
                   f"level range {lv.range_db:4.0f} dB  loudest {lv.loud_db:5.0f} dB")
    if not sys.stdin.isatty():
        typer.echo("Run this in a terminal to choose the mic track.")
        return
    from rich.console import Console

    folder = Path(cfg["output_dir"]).resolve() / f"{recording.stem}_tracks"
    chosen = tr.pick_streams(info, cfg, log=Console().print, folder=folder, force_ask=True, sample=sample)
    mic = chosen.get("mic")
    if mic and mic.how == "answer":
        typer.echo(f"Saved: track {mic.stream.position + 1} is your mic for recordings with these tracks "
                   f"({tr.KNOWN_TRACKS}).")
    typer.echo("Note: a mic name or index in your config's 'audio' section still comes first.")


@app.command()
def profiles():
    """List available game profiles."""
    for p in sorted(PROFILES_DIR.glob("*.yaml")):
        typer.echo(p.stem)


if __name__ == "__main__":
    app()
