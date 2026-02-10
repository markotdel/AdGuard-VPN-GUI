# Copyright (c) 2026 SubBotIn <markotdel@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations
import subprocess, re

import os
import shutil
from pathlib import Path

_ANSI_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")

def _clean_output(s: str) -> str:
    # Remove ANSI escape sequences and non-printable control chars
    if not s:
        return ""
    s = _ANSI_RE.sub("", s)
    s = "".join(ch for ch in s if ch == "\n" or ch == "\t" or (32 <= ord(ch) <= 0x10FFFF))
    return s.strip()

from dataclasses import dataclass

# Prefer the real upstream binary if installed. Wrapper scripts found in PATH
# may spawn sudo/askpass, which breaks GUI flows and user-level licensing.
_REAL_CLI = Path("/opt/adguardvpn_cli/adguardvpn-cli")
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


def _run_sudo(password: str, args: list[str], timeout: int = 90) -> str:
    # Run AdGuard VPN CLI as root via sudo, but preserve user's HOME/DBUS/DISPLAY so license/state stays in user profile.
    env_parts = []
    for k in ["HOME","USER","LOGNAME","XDG_RUNTIME_DIR","DBUS_SESSION_BUS_ADDRESS","DISPLAY","XAUTHORITY"]:
        v = os.environ.get(k)
        if v:
            env_parts.append(f"{k}={v}")
    cmd = ["sudo", "-S", "-p", ""] + (["env"] + env_parts if env_parts else []) + [CLI] + args
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

def _pkexec_env() -> list[str]:
    """Build pkexec env preserving current GUI session and user HOME.

    AdGuard VPN CLI may try to call sudo when it needs privileges, which
    fails from GUI without a terminal. Running the connect command via
    pkexec avoids that.

    We must keep HOME/XDG/DBUS of the current user so the CLI can access
    the user's license/token.
    """

    uid = os.getuid()
    home = os.environ.get("HOME", str(Path.home()))
    display = os.environ.get("DISPLAY", ":0")
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    dbus = os.environ.get("DBUS_SESSION_BUS_ADDRESS", f"unix:path={xdg_runtime}/bus")
    user = os.environ.get("USER", "")
    logname = os.environ.get("LOGNAME", user)
    return [
        f"HOME={home}",
        f"USER={user}",
        f"LOGNAME={logname}",
        f"DISPLAY={display}",
        f"XDG_RUNTIME_DIR={xdg_runtime}",
        f"DBUS_SESSION_BUS_ADDRESS={dbus}",
    ]


def _run_pkexec(args: list[str], timeout: int = 90) -> str:
    cli_path = _cli_realpath()
    cmd = ["pkexec", _which("env"), *_pkexec_env(), cli_path, *args]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    out = _clean_output(p.stdout or "")
    err = _clean_output(p.stderr or "")
    if p.returncode != 0:
        raise CliError(err or out or f"CLI error: {p.returncode}")
    return out


def connect_fastest() -> str:
    # run via pkexec so CLI doesn't try to spawn sudo/askpass
    return _run_pkexec(["connect", "--fastest", "-y"], timeout=90)


def connect_location(loc: str) -> str:
    return _run_pkexec(["connect", "-l", loc, "-y"], timeout=90)
def disconnect() -> str: return run(["disconnect"], timeout=30)


def _which(name: str) -> str:
    p = shutil.which(name)
    return p or name


def _cli_realpath() -> str:
    # Resolve adguardvpn-cli (symlinks included). If a wrapper script is found,
    # fall back to the real ELF binary in /opt.
    if Path(CLI).is_absolute():
        return str(Path(CLI).resolve())
    p = shutil.which(CLI)
    if not p:
        return CLI
    rp = Path(p).resolve()
    try:
        with open(rp, "rb") as f:
            head = f.read(4)
        if head != b"\x7fELF" and _REAL_CLI.exists():
            return str(_REAL_CLI)
    except Exception:
        if _REAL_CLI.exists():
            return str(_REAL_CLI)
    return str(rp)


def ensure_caps_for_connect() -> None:
    """Ensure adguardvpn-cli can start TUN without sudo.

    The upstream CLI may try to spawn sudo/askpass when lacking privileges.
    We grant required Linux capabilities to the CLI binary via pkexec+setcap,
    then run CLI as the current user (so license/keyring remain accessible).
    """

    cli_path = _cli_realpath()

    getcap = _which("getcap")
    try:
        p = subprocess.run([getcap, cli_path], capture_output=True, text=True)
        out = (p.stdout or "") + (p.stderr or "")
        if "cap_net_admin" in out:
            return
    except Exception:
        # If getcap is missing or fails, we still attempt setcap.
        pass

    setcap = _which("setcap")
    # Minimal set needed for TUN + raw sockets; bind_service is harmless if unused.
    caps = "cap_net_admin,cap_net_raw,cap_net_bind_service+ep"

    # pkexec will show a GUI password prompt via polkit
    p = subprocess.run(["pkexec", setcap, caps, cli_path], capture_output=True, text=True)
    if p.returncode != 0:
        err = _clean_output((p.stderr or "") + "\n" + (p.stdout or ""))
        raise CliError(err or "Не удалось выдать права (setcap).")


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
