from __future__ import annotations

import json
import os
from pathlib import Path


def app_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "simulcast"


def render_path() -> Path:
    return app_dir() / "render.json"


def load_render() -> dict:
    try:
        return json.loads(render_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_render(data: dict) -> None:
    try:
        path = render_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
