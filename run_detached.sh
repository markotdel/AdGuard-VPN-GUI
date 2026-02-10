#!/usr/bin/env bash
# Copyright (c) 2026 SubBotIn <markotdel@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later
# Detach launcher for AdGuard VPN GUI (so terminal doesn't "hold" the app).
# Use: ./run_detached.sh
set -euo pipefail
export PYTHONUNBUFFERED=1
# setsid detaches from controlling TTY
setsid -f adguardvpn-gui >/dev/null 2>&1 || nohup adguardvpn-gui >/dev/null 2>&1 &
