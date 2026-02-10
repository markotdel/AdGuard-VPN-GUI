#!/usr/bin/env bash
# Copyright (c) 2026 SubBotIn <markotdel@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

APP_ID="adguardvpn-gui"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DESKTOP_SRC="$ROOT_DIR/packaging/adguardvpn-gui.desktop"
ICON_SRC="$ROOT_DIR/src/adguardvpn_gui/ui/icons/adguardvpn.svg"

APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"

mkdir -p "$APPS_DIR" "$ICONS_DIR"

cp -f "$DESKTOP_SRC" "$APPS_DIR/adguardvpn-gui.desktop"
cp -f "$ICON_SRC" "$ICONS_DIR/adguardvpn.svg"

# Update desktop entry to point to current python entrypoint (installed script)
# (leave Exec=adguardvpn-gui)
echo "Installed desktop entry to: $APPS_DIR/adguardvpn-gui.desktop"
echo "Installed icon to:        $ICONS_DIR/adguardvpn.svg"
echo ""
echo "Now you can find 'AdGuard VPN' in your application menu."
echo "Tray icon should appear after launch (XFCE panel needs Status Tray plugin)."

# Try to refresh desktop database (optional)
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "${XDG_DATA_HOME:-$HOME/.local/share}/applications" >/dev/null 2>&1 || true
fi
