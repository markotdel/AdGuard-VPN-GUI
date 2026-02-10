# Copyright (c) 2026 SubBotIn <markotdel@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations
import gi
gi.require_version("Gtk","3.0")
gi.require_version("AyatanaAppIndicator3","0.1")
from gi.repository import Gtk, AyatanaAppIndicator3 as AppIndicator

class Tray:
    def __init__(self, icon_name_or_path: str, on_show, on_fastest, on_disconnect, on_quit):
        self.ind = AppIndicator.Indicator.new(
            "adguardvpn-gui",
            "adguardvpn",
            AppIndicator.IndicatorCategory.APPLICATION_STATUS,
        )
        # Prefer explicit file path when provided
        try:
            self.ind.set_icon_full(icon_name_or_path, "AdGuard VPN")
        except Exception:
            pass
        self.ind.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.ind.set_menu(self._menu(on_show,on_fastest,on_disconnect,on_quit))

    def _menu(self, on_show, on_fastest, on_disconnect, on_quit):
        m = Gtk.Menu()
        a = Gtk.MenuItem(label="Открыть")
        a.connect("activate", lambda *_: on_show())
        m.append(a)
        m.append(Gtk.SeparatorMenuItem())
        b = Gtk.MenuItem(label="Подключить (самая быстрая)")
        b.connect("activate", lambda *_: on_fastest())
        m.append(b)
        c = Gtk.MenuItem(label="Отключить")
        c.connect("activate", lambda *_: on_disconnect())
        m.append(c)
        m.append(Gtk.SeparatorMenuItem())
        q = Gtk.MenuItem(label="Выход")
        q.connect("activate", lambda *_: on_quit())
        m.append(q)
        m.show_all()
        return m
