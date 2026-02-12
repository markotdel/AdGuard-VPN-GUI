# Copyright (c) 2026 SubBotIn <markotdel@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations
import json, os
from pathlib import Path
from datetime import date

APP_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "adguardvpn-gui"
APP_DIR.mkdir(parents=True, exist_ok=True)
STATS_FILE = APP_DIR / "stats.json"
CONFIG_FILE = APP_DIR / "config.json"


def default_config() -> dict:
    return {
        # App-only preferences (not AdGuard VPN CLI config)
        "remember_last_location": True,
        "last_location": "",
        "sudo_password_enabled": False,
        "sudo_password": "",
        # UI preferences
        "lang": "ru",
        "window_width": 600,
        "window_height": 600,
    }


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return default_config()
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        base = default_config()
        if isinstance(data, dict):
            base.update({k: data.get(k, base[k]) for k in base.keys()})
        return base
    except Exception:
        return default_config()


def save_config(cfg: dict) -> None:
    base = default_config()
    out = {k: cfg.get(k, base[k]) for k in base.keys()}
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG_FILE)
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except Exception:
        pass

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
