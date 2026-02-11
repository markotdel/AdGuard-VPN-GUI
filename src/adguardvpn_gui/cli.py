# Copyright (c) 2026 SubBotIn <markotdel@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations
import subprocess, re

import os

_ANSI_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")

def _clean_output(s: str) -> str:
    # Remove ANSI escape sequences and non-printable control chars
    if not s:
        return ""
    s = _ANSI_RE.sub("", s)
    s = "".join(ch for ch in s if ch == "\n" or ch == "\t" or (32 <= ord(ch) <= 0x10FFFF))
    return s.strip()

from dataclasses import dataclass

from pathlib import Path

_REAL_CLI = Path("/opt/adguardvpn_cli/adguardvpn-cli")
# Prefer the real binary if it exists. Some wrappers named "adguardvpn-cli"
# may internally invoke sudo/askpass and cause auth loops.
CLI = str(_REAL_CLI) if _REAL_CLI.exists() else "adguardvpn-cli"

class CliError(RuntimeError):
    pass

def run(args: list[str], timeout: int = 30) -> str:
    p = subprocess.run([CLI] + args, capture_output=True, text=True, timeout=timeout)
    out = _clean_output(p.stdout or "")
    err = _clean_output(p.stderr or "")
    if p.returncode != 0:
        raise CliError(err or out or f"CLI error: {p.returncode}")
    return out



def _sudo_cli() -> str:
    return str(_REAL_CLI) if _REAL_CLI.exists() else CLI

def _run_sudo(password: str, args: list[str], timeout: int = 90) -> str:
    # Run CLI via sudo -S with preserved GUI/user env so license remains user-scoped.
    env_parts = []
    for k in ["HOME","USER","LOGNAME","XDG_RUNTIME_DIR","DBUS_SESSION_BUS_ADDRESS","DISPLAY","XAUTHORITY"]:
        v = os.environ.get(k)
        if v:
            env_parts.append(f"{k}={v}")
    cmd = ["sudo", "-S", "-p", ""] + (["env"] + env_parts if env_parts else []) + [_sudo_cli()] + args
    p = subprocess.run(cmd, input=(password or "") + "\n", capture_output=True, text=True, timeout=timeout)
    out = _clean_output(p.stdout or "")
    err = _clean_output(p.stderr or "")
    if p.returncode != 0:
        raise CliError(err or out or f"CLI error: {p.returncode}")
    return out


def connect_location_pw(loc: str, password: str) -> str:
    return _run_sudo(password, ["connect", "-l", loc, "-y"], timeout=120)


def connect_fastest_pw(password: str) -> str:
    return _run_sudo(password, ["connect", "--fastest", "-y"], timeout=120)


def status() -> str: return run(["status"], timeout=15)
def list_locations(count: int|None=None) -> str:
    return run(["list-locations"] + ([] if count is None else [str(count)]), timeout=60)

def connect_fastest() -> str: return run(["connect","--fastest","-y"], timeout=90)
def connect_location(loc: str) -> str: return run(["connect","-l",loc,"-y"], timeout=90)
def disconnect() -> str: return run(["disconnect"], timeout=30)

def disconnect_pw(password: str) -> str:
    return _run_sudo(password, ["disconnect"], timeout=60)

def config_show() -> str: return run(["config","show"], timeout=30)
def config_set_mode(v: str) -> str: return run(["config","set-mode",v], timeout=30)
def config_set_dns(v: str) -> str: return run(["config","set-dns",v], timeout=30)
def config_set_change_system_dns(v: str) -> str: return run(["config","set-change-system-dns",v], timeout=30)
def config_set_crash_reporting(v: str) -> str: return run(["config","set-crash-reporting",v], timeout=30)
def config_set_telemetry(v: str) -> str: return run(["config","set-telemetry",v], timeout=30)
def config_set_update_channel(v: str) -> str: return run(["config","set-update-channel",v], timeout=30)
def config_set_protocol(v: str) -> str: return run(["config","set-protocol",v], timeout=30)
def config_set_post_quantum(v: str) -> str: return run(["config","set-post-quantum",v], timeout=30)
def config_set_debug_logging(v: str) -> str: return run(["config","set-debug-logging",v], timeout=30)
def config_set_show_notifications(v: str) -> str: return run(["config","set-show-notifications",v], timeout=30)

def exclusions_mode_get() -> str: return run(["site-exclusions","mode"], timeout=30)
def exclusions_mode_set(v: str) -> str: return run(["site-exclusions","mode",v], timeout=30)
def exclusions_show() -> str: return run(["site-exclusions","show"], timeout=30)
def exclusions_add(items: list[str]) -> str: return run(["site-exclusions","add",*items], timeout=30)
def exclusions_remove(items: list[str]) -> str: return run(["site-exclusions","remove",*items], timeout=30)
def exclusions_clear() -> str: return run(["site-exclusions","clear"], timeout=30)

def export_logs(dirpath: str) -> str: return run(["export-logs","-o",dirpath,"-f"], timeout=120)

@dataclass
class VpnStatus:
    connected: bool
    location: str = ""
    mode: str = ""
    iface: str = ""

def parse_status(text: str) -> VpnStatus:
    t = (text or "").strip()
    if not t:
        return VpnStatus(False)
    if t.lower().startswith("connected to"):
        loc = ""
        mode = ""
        iface = ""
        try:
            loc = t.split("Connected to ",1)[1].split(" in ",1)[0].strip()
        except Exception:
            pass
        try:
            mode = t.split(" in ",1)[1].split(" mode",1)[0].strip().upper()
        except Exception:
            pass
        if "running on " in t:
            iface = t.split("running on ",1)[1].strip()
        return VpnStatus(True, loc, mode, iface)
    if "disconnected" in t.lower():
        return VpnStatus(False)
    return VpnStatus(False)

def parse_locations(text: str):
    lines = [ln.rstrip() for ln in (text or "").splitlines() if ln.strip()]
    rows = []
    started = False
    for ln in lines:
        if ln.startswith("ISO"):
            started = True
            continue
        if not started:
            continue
        if ln.startswith("You can connect"):
            break
        m = re.match(r"^(\S+)\s+(.+?)\s{2,}(.+?)\s{2,}(\d+)$", ln)
        if m:
            rows.append((m.group(1), m.group(2).strip(), m.group(3).strip(), int(m.group(4))))
    return rows
