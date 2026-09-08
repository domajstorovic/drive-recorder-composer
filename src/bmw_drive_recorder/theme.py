"""Optional HUD theme loaded from a small YAML subset (no PyYAML)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

Rgb = tuple[int, int, int]


@dataclass
class Theme:
    accent: Rgb = (0, 200, 255)
    card_bg: Rgb = (30, 30, 30)
    bar_bg: Rgb = (60, 60, 60)
    text: Rgb = (240, 240, 240)
    text_dim: Rgb = (160, 160, 160)
    font: Path | None = None
    speed_scale_kmh: float = 140.0
    speed_scale_mph: float = 90.0


def load_theme(path: Path) -> Theme:
    """Parse `key: value` YAML. Lists are `[r, g, b]`; `font` is a path."""
    path = path.resolve()
    data: dict[str, object] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, val = line.partition(":")
        if not sep:
            continue
        key = key.strip()
        val = val.strip()
        data[key] = _parse_value(val, path.parent)

    theme = Theme()
    for field in (
        "accent", "card_bg", "bar_bg", "text", "text_dim",
        "font", "speed_scale_kmh", "speed_scale_mph",
    ):
        if field in data:
            setattr(theme, field, data[field])
    return theme


def _parse_value(val: str, base: Path) -> object:
    if val.lower() in ("null", "none", "~", ""):
        return None
    if val.startswith("[") and val.endswith("]"):
        parts = [p.strip() for p in val[1:-1].split(",") if p.strip()]
        return tuple(int(p) for p in parts)
    if val.startswith(('"', "'")) and val.endswith(val[0]) and len(val) >= 2:
        val = val[1:-1]
    try:
        return float(val) if "." in val else int(val)
    except ValueError:
        p = Path(val)
        return p if p.is_absolute() else (base / p)
