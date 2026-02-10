# Copyright (c) 2026 SubBotIn <markotdel@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations
import json, os
from pathlib import Path
from datetime import date

APP_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "adguardvpn-gui"
APP_DIR.mkdir(parents=True, exist_ok=True)
STATS_FILE = APP_DIR / "stats.json"

def load_stats() -> dict:
    if not STATS_FILE.exists():
        return {"daily": {}}
    try:
        return json.loads(STATS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"daily": {}}

def save_stats(d: dict) -> None:
    STATS_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

def today_key() -> str:
    return date.today().isoformat()
