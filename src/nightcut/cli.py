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
        xml = Run(recording, cfg, set(force), folder).run()
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
def profiles():
    """List available game profiles."""
    for p in sorted(PROFILES_DIR.glob("*.yaml")):
        typer.echo(p.stem)


if __name__ == "__main__":
    app()
