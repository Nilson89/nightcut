from typer.testing import CliRunner

from nightcut import __version__
from nightcut.cli import app, required_models
from nightcut.config import load_config


def test_version_option():
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == f"nightcut {__version__}"


def test_version_matches_pyproject():
    import tomllib
    from pathlib import Path

    data = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["version"] == __version__


def test_required_models_default_config():
    assert required_models(load_config()) == ["qwen2.5vl:7b", "qwen3:14b"]


def test_required_models_skip_disabled_and_dedupe():
    cfg = {
        "vision": {"enabled": False, "model": "vis"},
        "killfeed": {"enabled": True, "model": None},
        "editor": {"enabled": True, "model": "edit"},
    }
    assert required_models(cfg) == ["vis", "edit"]  # kill feed still uses the vision model
    cfg["killfeed"]["model"] = "feed"
    cfg["editor"]["enabled"] = False
    assert required_models(cfg) == ["feed"]
