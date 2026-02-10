#!/usr/bin/env bash
# Copyright (c) 2026 SubBotIn <markotdel@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
APP_ID="adguardvpn-gui"
APP_NAME="AdGuard VPN"
APP_DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/${APP_ID}"
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"

need_cmd() { command -v "$1" >/dev/null 2>&1; }

echo "==> Uninstalling ${APP_NAME} (user-level)"
rm -f "$HOME/.local/bin/adguardvpn-gui"
rm -rf "$APP_DATA_DIR/venv" || true

rm -f "${APPS_DIR}/adguardvpn-gui.desktop"
rm -f "${ICONS_DIR}/adguardvpn.svg"

# Remove desktop shortcuts from common desktop locations
CAND=("$HOME/Desktop" "$HOME/Рабочий стол" "$HOME/Рабочий_стол")
if need_cmd xdg-user-dir; then
  d="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
  [ -n "$d" ] && CAND+=("$d")
fi
for d in "${CAND[@]}"; do
  [ -d "$d" ] && rm -f "$d/${APP_NAME}.desktop" || true
done

if need_cmd update-desktop-database; then
  update-desktop-database "${APPS_DIR}" >/dev/null 2>&1 || true
fi
if need_cmd xdg-desktop-menu; then
  xdg-desktop-menu forceupdate >/dev/null 2>&1 || true
fi
if need_cmd xfdesktop; then
  xfdesktop --reload >/dev/null 2>&1 || true
fi
echo "✅ Removed."
