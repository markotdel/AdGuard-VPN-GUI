#!/usr/bin/env bash
# Copyright (c) 2026 SubBotIn <markotdel@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later
# One-command installer for AdGuard VPN GUI (user-level, Ubuntu/Debian, XFCE-friendly)
# Usage:
#   unzip adguardvpn-gui_v0.3.3.zip
#   cd adguardvpn-gui
#   bash install.sh
set -euo pipefail

APP_NAME="AdGuard VPN"
APP_ID="adguardvpn-gui"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

need_cmd() { command -v "$1" >/dev/null 2>&1; }

echo "==> Installing ${APP_NAME} (GUI wrapper for adguardvpn-cli)"

if ! need_cmd python3; then
  echo "ERROR: python3 not found."
  exit 1
fi

echo "==> Installing system dependencies (requires sudo)"
sudo apt update
sudo apt install -y \
  python3 python3-venv python3-pip python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 \
  xdg-utils desktop-file-utils gir1.2-rsvg-2.0

CLI_PATH="$(command -v adguardvpn-cli || true)"
if [ -n "$CLI_PATH" ]; then
  echo "==> Configuring passwordless privileges for GUI usage (requires sudo)"

  # 1) Sudoers (best-effort). Some adguardvpn-cli builds spawn sudo internally, so we ALSO configure polkit below.
  SUDOERS_FILE="/etc/sudoers.d/adguardvpn-gui"
  RULE="${USER} ALL=(root) NOPASSWD: ${CLI_PATH} *"
  echo "$RULE" | sudo tee "$SUDOERS_FILE" >/dev/null
  sudo chmod 440 "$SUDOERS_FILE"
  sudo visudo -cf "$SUDOERS_FILE" >/dev/null

  # 2) Polkit rule for pkexec adguardvpn-cli (so GUI can run privileged commands without password)
  # Restrict to users in group "sudo" (Ubuntu default admin group).
  if command -v pkexec >/dev/null 2>&1; then
    POLKIT_RULE="/etc/polkit-1/rules.d/49-adguardvpn-gui.rules"
    echo "==> Installing polkit rule: $POLKIT_RULE"
    sudo tee "$POLKIT_RULE" >/dev/null <<EOF
polkit.addRule(function(action, subject) {
  try {
    if (action.id === "org.freedesktop.policykit.exec" &&
        action.lookup("program") === "${CLI_PATH}" &&
        subject.active === true && subject.local === true &&
        subject.isInGroup("sudo")) {
      return polkit.Result.YES;
    }
  } catch (e) {}
});
EOF
    sudo chmod 644 "$POLKIT_RULE"
  fi
else
  echo "WARNING: adguardvpn-cli not found in PATH. Install AdGuard VPN CLI first."
fi


# Create an app-managed venv (PEP 668 safe) and install there
APP_DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/${APP_ID}"
VENV_DIR="${APP_DATA_DIR}/venv"
mkdir -p "$APP_DATA_DIR"

echo "==> Creating/Updating virtualenv at: ${VENV_DIR}"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install -U pip setuptools wheel >/dev/null
"$VENV_DIR/bin/python" -m pip install -e "$ROOT_DIR" >/dev/null

# Stable launcher in ~/.local/bin
BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"

LAUNCHER="$BIN_DIR/adguardvpn-gui"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
exec "${VENV_DIR}/bin/python" -m adguardvpn_gui.main "\$@"
EOF
chmod +x "$LAUNCHER"

# Desktop integration (menu + desktop icon)
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
mkdir -p "$APPS_DIR" "$ICONS_DIR"

ICON_SRC="$ROOT_DIR/src/adguardvpn_gui/ui/icons/adguardvpn.svg"
cp -f "$ICON_SRC" "$ICONS_DIR/adguardvpn.svg"

DESKTOP_FILE="$APPS_DIR/adguardvpn-gui.desktop"
cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Version=1.0
Name=${APP_NAME}
Comment=GUI for AdGuard VPN CLI
Exec=${HOME}/.local/bin/adguardvpn-gui
Icon=adguardvpn
Terminal=false
Type=Application
Categories=Network;Security;
StartupNotify=true
EOF
chmod 644 "$DESKTOP_FILE"

# Determine desktop folder robustly (works with localized "Рабочий стол")
DESKTOP_DIR=""
if need_cmd xdg-user-dir; then
  DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
fi
if [ -z "$DESKTOP_DIR" ] || [ "$DESKTOP_DIR" = "$HOME" ]; then
  # Parse user-dirs.dirs if present
  if [ -f "$HOME/.config/user-dirs.dirs" ]; then
    DESKTOP_DIR="$(grep -E '^XDG_DESKTOP_DIR=' "$HOME/.config/user-dirs.dirs" | head -n1 | cut -d= -f2- | tr -d '"' | sed "s#\$HOME#$HOME#g")"
  fi
fi

# Fallback candidates
CANDIDATES=()
[ -n "$DESKTOP_DIR" ] && CANDIDATES+=("$DESKTOP_DIR")
CANDIDATES+=("$HOME/Desktop" "$HOME/Рабочий стол" "$HOME/Рабочий_стол")

SHORTCUT_NAME="${APP_NAME}.desktop"

for d in "${CANDIDATES[@]}"; do
  if [ -d "$d" ]; then
    cp -f "$DESKTOP_FILE" "$d/$SHORTCUT_NAME"
    chmod +x "$d/$SHORTCUT_NAME" || true
  fi
done

# Refresh caches (best-effort)
if need_cmd desktop-file-validate; then
  desktop-file-validate "$DESKTOP_FILE" >/dev/null 2>&1 || true
fi
if need_cmd update-desktop-database; then
  update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
fi
if need_cmd xdg-desktop-menu; then
  xdg-desktop-menu forceupdate >/dev/null 2>&1 || true
fi

# Restart desktop components to make icon/menu appear immediately (best-effort, no prompts)
if need_cmd xfdesktop; then
  xfdesktop --reload >/dev/null 2>&1 || true
fi
if need_cmd xfce4-panel; then
  xfce4-panel -r >/dev/null 2>&1 || true
fi

# Open the desktop folder so the user *sees* the shortcut (best-effort)
for d in "${CANDIDATES[@]}"; do
  if [ -d "$d" ]; then
    if need_cmd xdg-open; then
      xdg-open "$d" >/dev/null 2>&1 || true
    fi
    break
  fi
done

echo ""
echo "✅ Done."
echo "• Menu entry installed: ${DESKTOP_FILE}"
echo "• Launcher: ${HOME}/.local/bin/adguardvpn-gui"
echo "• Desktop shortcut created (in your Desktop folder)."