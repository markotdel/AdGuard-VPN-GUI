# Copyright (c) 2026 SubBotIn <markotdel@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations
import threading, time, re
import os, sys
import fcntl
import signal
from pathlib import Path
from datetime import date


# --- venv-friendly GI bootstrap (Ubuntu/Debian) ---
# Users often create a venv without --system-site-packages. In that case PyGObject (python3-gi),
# installed via apt, lives in /usr/lib/python3/dist-packages and is not visible in the venv.
# We auto-add common dist-packages paths so the app works with the exact quickstart commands.
def _ensure_gi_visible():
    import sys, os, glob
    try:
        import gi  # noqa: F401
        return
    except ModuleNotFoundError:
        pass

    candidates = [
        "/usr/lib/python3/dist-packages",
    ]
    candidates.extend(glob.glob("/usr/lib/python3*/dist-packages"))
    candidates.extend(glob.glob("/usr/lib/*/python3/dist-packages"))
    candidates.extend(glob.glob("/usr/local/lib/python3*/dist-packages"))

    for p in candidates:
        if p and os.path.isdir(p) and p not in sys.path:
            sys.path.append(p)

    import gi  # noqa: F401

_ensure_gi_visible()
# --- end bootstrap ---

import gi
gi.require_version("Gtk","3.0")
gi.require_version("Rsvg","2.0")
from gi.repository import Gtk, GLib, Gdk, Rsvg

import cairo

from . import cli, __version__
from .tray import Tray
from .utils import human_bytes
from .state import load_stats, save_stats, today_key, load_config, save_config

UI = Path(__file__).resolve().parent / "ui" / "main_window.ui"
CSS = Path(__file__).resolve().parent / "ui" / "style.css"
BG = Path(__file__).resolve().parent / "ui" / "assets" / "home_bg.svg"
ICON_CONNECTED = str((Path(__file__).resolve().parent / "ui" / "icons" / "adguardvpn.svg"))
ICON_DISCONNECTED = str((Path(__file__).resolve().parent / "ui" / "icons" / "disconnected_stop.svg"))

# --- lightweight localization for country/city display (keeps CLI codes intact) ---
COUNTRY_RU = {
    "US": "США",
    "GB": "Великобритания",
    "DE": "Германия",
    "FR": "Франция",
    "IT": "Италия",
    "ES": "Испания",
    "SE": "Швеция",
    "DK": "Дания",
    "EE": "Эстония",
    "LT": "Литва",
    "LV": "Латвия",
    "CH": "Швейцария",
    "AT": "Австрия",
    "BE": "Бельгия",
    "NL": "Нидерланды",
    "IE": "Ирландия",
    "PL": "Польша",
    "CZ": "Чехия",
    "NO": "Норвегия",
    "FI": "Финляндия",
    "IS": "Исландия",
    "RU": "Россия",
    "MD": "Молдова",
    "PT": "Португалия",
    "IL": "Израиль",
    "EG": "Египет",
    "HU": "Венгрия",
    "RS": "Сербия",
    "BG": "Болгария",
    "CY": "Кипр",
    "IR": "Иран",
}

CITY_RU = {
    # frequent
    "New York": "Нью-Йорк",
    "Stockholm": "Стокгольм",
    "Copenhagen": "Копенгаген",
    "Tallinn": "Таллин",
    "Vilnius": "Вильнюс",
    "Zurich": "Цюрих",
    "Riga": "Рига",
    "Chisinau": "Кишинёв",
    "London": "Лондон",
    "Berlin": "Берлин",
    "Frankfurt": "Франкфурт",
    "Vienna": "Вена",
    "Prague": "Прага",
    "Dublin": "Дублин",
    "Warsaw": "Варшава",
    "Amsterdam": "Амстердам",
    "Brussels": "Брюссель",
    "Oslo": "Осло",
    "Helsinki": "Хельсинки",
    "Reykjavik": "Рейкьявик",
    "Moscow": "Москва",
    "Moscow(Virtual)": "Москва (Virtual)",
}

_TR_BASIC = (
    ("shch", "щ"), ("sch", "щ"),
    ("yo", "ё"), ("yu", "ю"), ("ya", "я"),
    ("zh", "ж"), ("kh", "х"), ("ts", "ц"),
    ("ch", "ч"), ("sh", "ш"),
)
_TR_CHARS = {
    "a": "а", "b": "б", "c": "к", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "х",
    "i": "и", "j": "дж", "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п",
    "q": "к", "r": "р", "s": "с", "t": "т", "u": "у", "v": "в", "w": "в", "x": "кс",
    "y": "й", "z": "з",
}


def _latin_to_ru(text: str) -> str:
    """Very small transliteration helper for city names.

    It's not perfect (and can't be without a full dictionary), but keeps UI consistent.
    """
    if not text:
        return text
    # keep parentheses part intact
    s = text
    # handle hyphenated names
    parts = re.split(r"(\s+|-)" , s)
    out_parts = []
    for p in parts:
        if p.isspace() or p == "-":
            out_parts.append(p)
            continue
        low = p.lower()
        # quick substitutions
        for src, dst in _TR_BASIC:
            low = low.replace(src, dst)
        buf = []
        i = 0
        while i < len(low):
            ch = low[i]
            if ch in "щёжюяцчш":
                buf.append(ch)
                i += 1
                continue
            if ch in _TR_CHARS:
                buf.append(_TR_CHARS[ch])
            else:
                buf.append(ch)
            i += 1
        # Capitalize first letter
        word = "".join(buf)
        if word:
            word = word[0].upper() + word[1:]
        out_parts.append(word)
    return "".join(out_parts)

def run_bg(fn, ok, err):
    def _t():
        try:
            r = fn()
            GLib.idle_add(ok, r)
        except Exception as e:
            GLib.idle_add(err, e)
    threading.Thread(target=_t, daemon=True).start()

def parse_config(text: str) -> dict:
    d = {}
    for ln in (text or "").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("Current configuration"):
            continue
        if ":" in ln:
            k,v = ln.split(":",1)
            d[k.strip().lower()] = v.strip()
    return d

def onoff(b: bool) -> str:
    return "on" if b else "off"

def bool_on(v: str) -> bool:
    return str(v).strip().lower() in ("on","true","yes","enabled")

def iface_bytes(iface: str):
    p = Path("/sys/class/net")/iface/"statistics"
    try:
        rx = int((p/"rx_bytes").read_text().strip())
        tx = int((p/"tx_bytes").read_text().strip())
        return rx, tx
    except Exception:
        return -1, -1

class App:
    def __init__(self):
        self.cfg = load_config()
        self.b = Gtk.Builder.new_from_file(str(UI))
        self.win: Gtk.Window = self.b.get_object("main_window")
        self.win.set_default_size(1280, 720)
        title = f"AdGuard VPN-GUI v{__version__}"
        self.win.set_title(title)
        try:
            hb = self.b.get_object("headerbar")
            if hb:
                hb.set_title(title)
        except Exception:
            pass
        self.win.set_resizable(True)
        self.win.connect("delete-event", self.on_close_to_tray)

        self.notebook: Gtk.Notebook = self.b.get_object("notebook")
        self.btn_refresh_all: Gtk.Button = self.b.get_object("btn_refresh_all")

        # Home
        # Older UI builds used GtkImage("img_home_bg"), newer ones may use GtkDrawingArea("da_home_bg").
        self.img_home_bg = self.b.get_object("img_home_bg")
        self.da_home_bg = self.b.get_object("da_home_bg")
        self.home_title: Gtk.Label = self.b.get_object("home_title_connected")
        self.home_sub: Gtk.Label = self.b.get_object("home_subtitle")
        self.home_sub.set_line_wrap(True)
        self.home_sub.set_max_width_chars(48)
        self.home_sub.set_ellipsize(0)
        self.btn_connect: Gtk.Button = self.b.get_object("btn_connect")
        self.btn_disconnect: Gtk.Button = self.b.get_object("btn_disconnect")
        self.btn_fastest: Gtk.Button = self.b.get_object("btn_fastest")
        self.entry_search: Gtk.SearchEntry = self.b.get_object("entry_search")
        self.lbl_current_location: Gtk.Label = self.b.get_object("lbl_current_location")
        self.lbl_current_ping: Gtk.Label = self.b.get_object("lbl_current_ping")
        self.lbl_country_city: Gtk.Label = self.b.get_object("lbl_country_city")
        self.lbl_license: Gtk.Label = self.b.get_object("lbl_license")
        self.tv_fast: Gtk.TreeView = self.b.get_object("tv_fast")
        self.tv_all: Gtk.TreeView = self.b.get_object("tv_all")

        # Info
        self.infobar: Gtk.InfoBar = self.b.get_object("infobar")
        self.lbl_info: Gtk.Label = self.b.get_object("lbl_info")

        # Exclusions
        self.lbl_excl_mode: Gtk.Label = self.b.get_object("lbl_excl_mode")
        self.cmb_excl_mode: Gtk.ComboBoxText = self.b.get_object("cmb_excl_mode")
        self.btn_excl_apply_mode: Gtk.Button = self.b.get_object("btn_excl_apply_mode")
        self.entry_excl_add: Gtk.Entry = self.b.get_object("entry_excl_add")
        self.btn_excl_add: Gtk.Button = self.b.get_object("btn_excl_add")
        self.btn_excl_remove: Gtk.Button = self.b.get_object("btn_excl_remove")
        self.btn_excl_clear: Gtk.Button = self.b.get_object("btn_excl_clear")
        self.tv_excl: Gtk.TreeView = self.b.get_object("tv_excl")

        # Stats
        self.lbl_stat_iface_v: Gtk.Label = self.b.get_object("lbl_stat_iface_v")
        self.lbl_stat_in_v: Gtk.Label = self.b.get_object("lbl_stat_in_v")
        self.lbl_stat_out_v: Gtk.Label = self.b.get_object("lbl_stat_out_v")
        self.lbl_stat_session_v: Gtk.Label = self.b.get_object("lbl_stat_session_v")
        self.tv_stats_history: Gtk.TreeView = self.b.get_object("tv_stats_history")

        # Support
        self.fc_logs_dir: Gtk.FileChooserButton = self.b.get_object("fc_logs_dir")
        self.btn_export_logs: Gtk.Button = self.b.get_object("btn_export_logs")
        self.lbl_support_out: Gtk.Label = self.b.get_object("lbl_support_out")

        # Settings
        self.cmb_mode: Gtk.ComboBoxText = self.b.get_object("cmb_mode")
        self.entry_dns: Gtk.Entry = self.b.get_object("entry_dns")
        self.sw_change_dns: Gtk.Switch = self.b.get_object("sw_change_dns")
        self.sw_crash: Gtk.Switch = self.b.get_object("sw_crash")
        self.sw_telemetry: Gtk.Switch = self.b.get_object("sw_telemetry")
        self.cmb_protocol: Gtk.ComboBoxText = self.b.get_object("cmb_protocol")
        self.sw_pq: Gtk.Switch = self.b.get_object("sw_pq")
        self.cmb_update_channel: Gtk.ComboBoxText = self.b.get_object("cmb_update_channel")
        self.sw_debug: Gtk.Switch = self.b.get_object("sw_debug")
        self.sw_notify: Gtk.Switch = self.b.get_object("sw_notify")
        # App preferences (GUI-only)
        self.sw_remember_last_loc: Gtk.Switch = self.b.get_object("sw_remember_last_loc")
        self.sw_sudo_pwd: Gtk.Switch = self.b.get_object("sw_sudo_pwd")
        self.ent_sudo_pwd: Gtk.Entry = self.b.get_object("ent_sudo_pwd")
        self.cmb_lang: Gtk.ComboBoxText = self.b.get_object("cmb_lang")
        self.scale_win_w: Gtk.Scale = self.b.get_object("scale_win_w")
        self.scale_win_h: Gtk.Scale = self.b.get_object("scale_win_h")
        self.btn_settings_reload: Gtk.Button = self.b.get_object("btn_settings_reload")
        self.btn_settings_apply: Gtk.Button = self.b.get_object("btn_settings_apply")
        self.lbl_settings_out: Gtk.Label = self.b.get_object("lbl_settings_out")

        self._apply_css()
        self._apply_home_styles()
        self._setup_models()

        self._locations_text_cache = ""
        self._license_text_cache = None
        self._license_fetch_inflight = False
        self._last_license_fetch = 0.0

        # Signals
        self.btn_refresh_all.connect("clicked", lambda *_: self._on_refresh_clicked())
        self.btn_fastest.connect("clicked", lambda *_: self.connect_fastest())
        self.btn_connect.connect("clicked", lambda *_: self.connect_selected())
        self.btn_disconnect.connect("clicked", lambda *_: self.disconnect())
        self.entry_search.connect("search-changed", lambda *_: self.store_all_filtered.refilter())
        self.tv_fast.connect("row-activated", self.on_row_activated)
        self.tv_all.connect("row-activated", self.on_row_activated)

        self.btn_excl_apply_mode.connect("clicked", lambda *_: self.excl_apply_mode())
        self.btn_excl_add.connect("clicked", lambda *_: self.excl_add())
        self.btn_excl_remove.connect("clicked", lambda *_: self.excl_remove_selected())
        self.btn_excl_clear.connect("clicked", lambda *_: self.excl_clear())

        self.btn_export_logs.connect("clicked", lambda *_: self.export_logs())

        self.btn_settings_reload.connect("clicked", lambda *_: self.settings_reload())
        self.btn_settings_apply.connect("clicked", lambda *_: self.settings_apply())

        # Apply persisted app-only settings to UI
        self._load_app_prefs_to_ui()
        self._apply_language()
        self._apply_window_size()

        # Persist app-only settings on change
        self.sw_remember_last_loc.connect("notify::active", lambda *_: self._save_app_prefs_from_ui())
        self.sw_sudo_pwd.connect("notify::active", lambda *_: self._on_sudo_toggle())
        self.ent_sudo_pwd.connect("changed", lambda *_: self._on_sudo_entry_changed())
        self.cmb_lang.connect("changed", lambda *_: self._on_lang_changed())

        # Tray
        # Tray "Выход" должен отключать VPN и затем закрывать приложение.
        self.tray = Tray(ICON_CONNECTED, ICON_DISCONNECTED, self.show, self.connect_fastest, self.disconnect, self.quit_from_tray)

        # Periodically poll VPN status so tray icon / кнопки синхронизируются
        # даже если пользователь подключается/отключается через терминал.
        self._poll_inflight = False
        GLib.timeout_add_seconds(3, self._poll_status)

        self.current_iface = "tun0"
        self.connected_since = None
        self._last_rx = None
        self._last_tx = None
        GLib.timeout_add(1000, self._tick_stats)

        # One-time apply last-location selection after the first locations load.
        self._startup_loc_applied = False

        self.refresh_all()

        # Keep UI + tray icon in sync with CLI actions done outside the GUI
        # (e.g., user runs adguardvpn-cli in terminal).
        GLib.timeout_add_seconds(2, self._poll_status)

    def _apply_css(self):
        prov = Gtk.CssProvider()
        prov.load_from_path(str(CSS))
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _apply_home_styles(self):
        # Set / draw background image
        if self.img_home_bg is not None:
            # Legacy GtkImage background
            try:
                self.img_home_bg.set_from_file(str(BG))
            except Exception:
                pass
        elif self.da_home_bg is not None:
            # Draw SVG scaled to widget size
            try:
                self._bg_svg = Rsvg.Handle.new_from_file(str(BG))
            except Exception:
                self._bg_svg = None

            def _draw_bg(area, cr: cairo.Context):
                if not self._bg_svg:
                    return False
                alloc = area.get_allocation()
                w, h = max(1, alloc.width), max(1, alloc.height)
                try:
                    dim = self._bg_svg.get_dimensions()
                    sw, sh = max(1, dim.width), max(1, dim.height)
                except Exception:
                    sw, sh = 1920, 1080

                sx, sy = w / sw, h / sh
                s = max(sx, sy)  # cover
                tx = (w - sw * s) / 2.0
                ty = (h - sh * s) / 2.0

                cr.save()
                cr.translate(tx, ty)
                cr.scale(s, s)
                try:
                    self._bg_svg.render_cairo(cr)
                except Exception:
                    # Fallback to old API name
                    try:
                        self._bg_svg.render_cairo(cr)
                    except Exception:
                        pass
                cr.restore()
                return False

            # Connect once
            if not getattr(self, "_bg_draw_connected", False):
                self.da_home_bg.connect("draw", _draw_bg)
                self._bg_draw_connected = True

        # Named styles
        self.b.get_object("home_left_panel").set_name("home_left_panel")
        self.home_title.set_name("home_title_connected")
        self.home_sub.set_name("home_subtitle")

        # Buttons: primary (white) for Disconnect like in screenshot, secondary for Connect (transparent)
        self.btn_disconnect.set_name("disconnect_btn")
        self.btn_connect.set_name("connect_btn")

    def _setup_models(self):
        self.btn_disconnect.hide()
        # Locations
        # columns: iso, country_display, city_display, ping, city_real
        self.store_fast = Gtk.ListStore(str, str, str, int, str)
        self.store_all = Gtk.ListStore(str, str, str, int, str)
        self.store_all_filtered = self.store_all.filter_new()
        self.store_all_filtered.set_visible_func(self._filter_all)

        self._setup_tree(self.tv_fast, self.store_fast)
        self._setup_tree(self.tv_all, self.store_all_filtered)

        # Exclusions
        self.store_excl = Gtk.ListStore(str)
        self.tv_excl.set_model(self.store_excl)
        self.tv_excl.set_headers_visible(True)
        self.tv_excl.append_column(Gtk.TreeViewColumn("Домен", Gtk.CellRendererText(), text=0))

        # Stats history
        self.store_hist = Gtk.ListStore(str,str,str)
        self.tv_stats_history.set_model(self.store_hist)
        self.tv_stats_history.set_headers_visible(True)
        for i, name in enumerate(["Дата","RX","TX"]):
            self.tv_stats_history.append_column(Gtk.TreeViewColumn(name, Gtk.CellRendererText(), text=i))

    def _setup_tree(self, tv: Gtk.TreeView, store):
        tv.set_model(store)
        tv.set_headers_visible(True)
        titles = [("ISO",0),("Страна",1),("Город",2),("Пинг",3)]
        for title, idx in titles:
            r = Gtk.CellRendererText()
            if title == "Пинг":
                r.set_property("xalign", 1.0)
            tv.append_column(Gtk.TreeViewColumn(title, r, text=idx))

    def _filter_all(self, model, it, data=None):
        q = (self.entry_search.get_text() or "").strip().lower()
        if not q:
            return True
        iso, country, city, ping, _city_real = model[it]
        return q in (iso or "").lower() or q in (country or "").lower() or q in (city or "").lower()

    def info(self, msg: str, err=False):
        self.lbl_info.set_text(msg)
        self.infobar.set_message_type(Gtk.MessageType.ERROR if err else Gtk.MessageType.INFO)
        self.infobar.set_visible(True)
        GLib.timeout_add(3500, self._hide_infobar)


    def _ask_sudo_password(self) -> str|None:
        # If user opted in to storing the password, use it.
        try:
            self.cfg = load_config()
            if self.cfg.get("sudo_password_enabled") and (self.cfg.get("sudo_password") or "").strip():
                return str(self.cfg.get("sudo_password")).strip()
        except Exception:
            pass

        dlg = Gtk.Dialog(title="Введите пароль sudo", parent=self.win, flags=0)
        dlg.add_button("Отмена", Gtk.ResponseType.CANCEL)
        dlg.add_button("OK", Gtk.ResponseType.OK)
        box = dlg.get_content_area()
        box.set_spacing(8)
        lbl = Gtk.Label(label="Для подключения требуется пароль sudo.")
        lbl.set_xalign(0)
        entry = Gtk.Entry()
        entry.set_visibility(False)
        entry.set_invisible_char("•")
        entry.set_placeholder_text("Пароль sudo")
        entry.set_activates_default(True)
        dlg.set_default_response(Gtk.ResponseType.OK)
        box.add(lbl)
        box.add(entry)
        dlg.show_all()
        resp = dlg.run()
        pwd = entry.get_text() if resp == Gtk.ResponseType.OK else None
        dlg.destroy()
        if pwd is not None:
            pwd = pwd.strip()
        return pwd or None

    def _hide_infobar(self):
        self.infobar.set_visible(False)
        return False

    def show(self):
        self.win.show_all()
        self.win.present()


    def _draw_home_bg(self, widget, cr):
        # Scale the SVG background to fill the available area (no empty space)
        try:
            alloc = widget.get_allocation()
            w = max(1, alloc.width)
            h = max(1, alloc.height)
            dim = self._bg_handle.get_dimensions()
            sw = max(1, dim.width)
            sh = max(1, dim.height)
            sx = w / sw
            sy = h / sh
            cr.save()
            cr.scale(sx, sy)
            self._bg_handle.render_cairo(cr)
            cr.restore()
        except Exception:
            pass
        return False

    def quit(self):
        Gtk.main_quit()

    def quit_from_tray(self):
        """Tray menu -> Exit: disconnect VPN then quit."""
        # Avoid double-trigger
        if getattr(self, "_quitting", False):
            return
        self._quitting = True

        def _do_quit(_=None):
            Gtk.main_quit()

        # Best-effort disconnect; even if it fails (or user cancels auth), we still quit.
        pwd = self._ask_sudo_password()
        if not pwd:
            run_bg(lambda: cli.disconnect(), lambda *_: _do_quit(), lambda *_: _do_quit())
            return
        run_bg(lambda: cli.disconnect_pw(pwd), lambda *_: _do_quit(), lambda *_: _do_quit())

    def _poll_status(self):
        """Lightweight status sync for tray/icon/buttons."""
        if getattr(self, "_quitting", False):
            return False
        if self._poll_inflight:
            return True
        self._poll_inflight = True

        def _bg():
            return cli.status()

        def _ok(st_text: str):
            self._poll_inflight = False
            try:
                st = cli.parse_status(st_text)
                # Reuse last known fast locations text for ping label if any.
                fast_text = getattr(self, "_last_fast_text", "")
                self._render_status(st, st_text, fast_text)
            except Exception:
                pass

        def _err(_e: Exception):
            self._poll_inflight = False
        run_bg(_bg, _ok, _err)
        return True

    # (removed duplicated quit_from_tray/_poll_status definitions)

    def on_close_to_tray(self, *_):
        self.win.hide()
        return True


    def _t(self, key: str) -> str:
        lang = str(self.cfg.get("lang") or "ru")
        tr = {
            "ru": {
                "btn_connect": "Подключить",
                "btn_disconnect": "Отключить",
                "status_connected_title": "Подключён",
                "status_disconnected_title": "Отключён",
                "status_connected_sub": "Подключено к {loc} (режим: {mode}, интерфейс: {iface})",
                "status_disconnected_sub": "VPN отключён",
                "country_city": "Страна: {country}   Город: {city}",
                "country_city_unknown": "Страна: —   Город: —",
                "license_prefix": "Лицензия: ",
                "update_checking": "Проверяю обновления…",
                "update_available": "Доступно обновление: {ver}. Нажмите «Обновить», чтобы установить.",
                "update_none": "Обновлений нет.",
                "update_failed": "Не удалось проверить обновления.",
                "update_installing": "Скачиваю и устанавливаю обновление…",
                "update_done": "Обновление установлено. Перезапустите программу.",
                "settings_lang": "Язык:",
            },
            "en": {
                "btn_connect": "Connect",
                "btn_disconnect": "Disconnect",
                "status_connected_title": "Connected",
                "status_disconnected_title": "Disconnected",
                "status_connected_sub": "Connected to {loc} (mode: {mode}, iface: {iface})",
                "status_disconnected_sub": "VPN is disconnected",
                "country_city": "Country: {country}   City: {city}",
                "country_city_unknown": "Country: —   City: —",
                "license_prefix": "License: ",
                "update_checking": "Checking updates…",
                "update_available": "Update available: {ver}. Click “Update” to install.",
                "update_none": "No updates.",
                "update_failed": "Update check failed.",
                "update_installing": "Downloading and installing update…",
                "update_done": "Update installed. Please restart the app.",
                "settings_lang": "Language:",
            },
            "de": {
                "btn_connect": "Verbinden",
                "btn_disconnect": "Trennen",
                "status_connected_title": "Verbunden",
                "status_disconnected_title": "Getrennt",
                "status_connected_sub": "Verbunden mit {loc} (Modus: {mode}, Schnittstelle: {iface})",
                "status_disconnected_sub": "VPN ist getrennt",
                "country_city": "Land: {country}   Stadt: {city}",
                "country_city_unknown": "Land: —   Stadt: —",
                "license_prefix": "Lizenz: ",
                "update_checking": "Suche nach Updates…",
                "update_available": "Update verfügbar: {ver}. Klicken Sie auf „Update“, um zu installieren.",
                "update_none": "Keine Updates.",
                "update_failed": "Update-Prüfung fehlgeschlagen.",
                "update_installing": "Update wird heruntergeladen und installiert…",
                "update_done": "Update installiert. Bitte App neu starten.",
                "settings_lang": "Sprache:",
            },
        }
        if lang not in tr:
            lang = "ru"
        return tr[lang].get(key, key)

    def _localize_country_city(self, iso: str, country: str, city: str) -> tuple[str, str]:
        """Localized display names for the current UI language."""
        lang = str(self.cfg.get("lang") or "ru")
        iso = (iso or "").strip().upper()
        country_in = (country or "").strip()
        city_in = (city or "").strip()

        # English & German: show as-is
        if lang in ("en", "de"):
            return (country_in or iso, city_in)

        # Russian
        country_out = COUNTRY_RU.get(iso, country_in or iso)

        # Try direct map first, then a lightweight transliteration
        key = city_in
        city_out = CITY_RU.get(key)
        if city_out is None:
            # status output often comes like "NEW YORK" – normalize to Title Case
            norm = " ".join([w.capitalize() for w in key.replace("_", " ").split()])
            city_out = CITY_RU.get(norm)
        if city_out is None:
            city_out = _latin_to_ru(city_in)
        return (country_out, city_out)

    def _apply_language(self):
        # Buttons on the home card
        try:
            self.btn_connect.set_label(self._t("btn_connect"))
            self.btn_disconnect.set_label(self._t("btn_disconnect"))
        except Exception:
            pass

        # Settings label
        try:
            lbl = self.b.get_object("lbl_lang")
            if lbl:
                lbl.set_text(self._t("settings_lang"))
        except Exception:
            pass

    def _apply_window_size(self):
        """Force a stable window size (prevents GTK from auto-growing after Apply)."""
        try:
            w = int(self.cfg.get("window_width", 600) or 600)
            h = int(self.cfg.get("window_height", 600) or 600)
            w = max(400, min(1600, w))
            h = max(400, min(1600, h))
            self.win.set_default_size(w, h)
            # resize() matters if the window is already realized
            self.win.resize(w, h)
        except Exception:
            pass

    def _kick_license_refresh(self):
        import time
        if self._license_fetch_inflight:
            return
        # refresh at most once per 60s
        if time.time() - float(self._last_license_fetch or 0) < 60 and self._license_text_cache:
            self.lbl_license.set_text(self._t("license_prefix") + self._license_text_cache)
            return

        self._license_fetch_inflight = True

        def worker():
            import time
            try:
                out = cli.license()
                # keep it short: first non-empty 3 lines
                lines = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
                short = " | ".join(lines[:3]) if lines else "—"
                # Localize common license phrases for UI (best-effort)
                # Localize common license phrases for UI (best-effort)
                lang = self.cfg.get('lang', 'ru')
                if lang == 'ru' and short != '—':
                    for a,b in [
                        ('Logged in as', 'Вход выполнен как'),
                        ('You are using the PREMIUM version', 'Премиум версия'),
                        ('Up to ', 'До '),
                        ('devices simultaneously', 'устройств одновременно'),
                        ('Your subscription is valid until', 'Подписка действует до'),
                    ]:
                        short = short.replace(a, b)
                elif lang == 'de' and short != '—':
                    for a,b in [
                        ('Logged in as', 'Angemeldet als'),
                        ('You are using the PREMIUM version', 'PREMIUM-Version'),
                        ('devices simultaneously', 'Geräte gleichzeitig'),
                        ('Your subscription is valid until', 'Abo gültig bis'),
                    ]:
                        short = short.replace(a, b)
                self._license_text_cache = short
                self._last_license_fetch = time.time()
                GLib.idle_add(self.lbl_license.set_text, self._t("license_prefix") + short)
            except Exception:
                GLib.idle_add(self.lbl_license.set_text, self._t("license_prefix") + "—")
            finally:
                self._license_fetch_inflight = False

        threading.Thread(target=worker, daemon=True).start()

    def _on_refresh_clicked(self):
        self.refresh_all()
        self._check_app_updates_async()

    def _check_app_updates_async(self):
        # GitHub releases check (best-effort, async)
        def worker():
            try:
                GLib.idle_add(self.set_info, self._t("update_checking"))
                latest = self._get_latest_release_version()
                if not latest:
                    GLib.idle_add(self.set_info, self._t("update_failed"))
                    return
                if self._is_newer_version(latest, __version__):
                    GLib.idle_add(self.set_info, self._t("update_available").format(ver=latest))
                else:
                    GLib.idle_add(self.set_info, self._t("update_none"))
            except Exception:
                GLib.idle_add(self.set_info, self._t("update_failed"))
        threading.Thread(target=worker, daemon=True).start()

    def _get_latest_release_version(self) -> str | None:
        import json, urllib.request
        req = urllib.request.Request(
            "https://api.github.com/repos/markotdel/AdGuard-VPN-GUI/releases/latest",
            headers={"User-Agent": "adguardvpn-gui"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
        tag = (data.get("tag_name") or data.get("name") or "").strip()
        if tag.startswith("v"):
            tag = tag[1:]
        return tag or None

    def _is_newer_version(self, a: str, b: str) -> bool:
        def norm(v: str):
            parts = [p for p in v.split(".") if p.isdigit() or p.isnumeric()]
            nums = []
            for p in v.split("."):
                try:
                    nums.append(int(re.sub(r"[^0-9]", "", p) or "0"))
                except Exception:
                    nums.append(0)
            return nums + [0] * (4 - len(nums))
        na = norm(a)
        nb = norm(b)
        return na > nb
    def _set_settings_out(self, msg: str) -> None:
        """Prevent settings output from exploding window size on some GTK themes."""
        if msg is None:
            msg = ""
        # Collapse newlines and trim
        s = ' '.join(str(msg).split())
        if len(s) > 260:
            s = s[:260] + '…'
        self.lbl_settings_out.set_text(s)




    def refresh_all(self):
        self.info("Обновляю…")
        run_bg(lambda: (cli.status(), cli.list_locations(10), cli.list_locations(), cli.exclusions_mode_get(), cli.exclusions_show(), cli.config_show()),
               self._on_refresh_ok,
               self._on_refresh_err)

    def _on_refresh_ok(self, res):
        st_text, fast_text, all_text, mode_text, excl_text, cfg_text = res
        self._locations_text_cache = all_text or ""
        # Keep last known fastest list for ping lookup in lightweight status polls.
        self._last_fast_text = fast_text
        st = cli.parse_status(st_text)
        self._render_status(st, st_text, fast_text)
        self._render_locations(fast_text, all_text)
        self._render_exclusions(mode_text, excl_text)
        self._render_settings(cfg_text)
        self._render_history(load_stats())
        self.info("Готово")

    def _on_refresh_err(self, e: Exception):
        self.info(f"Ошибка: {e}", err=True)

    def _render_status(self, st: cli.VpnStatus, raw: str, fast_text: str):
        # Remember last known state for UI handlers (e.g. double-click on a location)
        self._last_connected = bool(getattr(st, "connected", False))

        # buttons & title
        if st.connected:
            self.home_title.set_text(self._t("status_connected_title"))
            self.btn_connect.hide()
            self.btn_disconnect.show()
        else:
            self.home_title.set_text(self._t("status_disconnected_title"))
            self.btn_disconnect.hide()
            self.btn_connect.show()

        # Tray icon must follow actual connection state (also when user runs CLI manually)
        try:
            self.tray.set_connected(bool(st.connected))
        except Exception:
            pass

        # subtitle (localized, not raw CLI text)
        if st.connected:
            loc_raw = st.location or ""
            mode = st.mode or "—"
            iface = st.iface or "—"
            # try to localize location display
            _c, loc_disp = self._country_city_for_location(loc_raw, fast_text)
            if not loc_disp:
                _c, loc_disp = self._country_city_for_location(loc_raw, self._locations_text_cache or "")
            loc_disp = (loc_disp or loc_raw or "—")
            self.home_sub.set_text(self._t("status_connected_sub").format(loc=loc_disp, mode=mode, iface=iface))
        else:
            self.home_sub.set_text(self._t("status_disconnected_sub"))

        # Current location label on the card (kept)
        # Current location label on the card (localized)
        if st.connected:
            self.lbl_current_location.set_text((loc_disp or "—").upper())
        else:
            self.lbl_current_location.set_text("—")

        # Country / City line
        country, city = self._country_city_for_location(st.location or "", fast_text)
        if not country:
            # fallback: try full locations list
            country, city = self._country_city_for_location(st.location or "", self._locations_text_cache or "")
        if country and city:
            self.lbl_country_city.set_text(self._t("country_city").format(country=country, city=city))
        else:
            self.lbl_country_city.set_text(self._t("country_city_unknown"))

        # Ping line
        if st.connected and st.location:
            p = self._ping_for_location(st.location, fast_text)
            self.lbl_current_ping.set_text(str(p) if p is not None else "—")
        else:
            self.lbl_current_ping.set_text("—")

        # License info (best-effort, async)
        self._kick_license_refresh()


    def _country_city_for_location(self, loc: str, locations_text: str):
        loc = (loc or "").strip().lower()
        if not loc:
            return (None, None)
        for iso, country, city, ping in cli.parse_locations(locations_text):
            if city.strip().lower() == loc:
                country_disp, city_disp = self._localize_country_city(iso, country, city)
                return (country_disp, city_disp)
        return (None, None)

    def _ping_for_location(self, loc: str, locations_text: str):
        loc = (loc or "").strip().lower()
        if not loc:
            return None
        for iso,country,city,ping in cli.parse_locations(locations_text):
            if city.strip().lower() == loc:
                return ping
        return None

    def _render_locations(self, fast_text: str, all_text: str):
        self.store_fast.clear()
        self.store_all.clear()
        for row in cli.parse_locations(fast_text):
            iso, country, city, ping = row
            country_disp, city_disp = self._localize_country_city(iso, country, city)
            self.store_fast.append([iso, country_disp, city_disp, int(ping), city])
        for row in cli.parse_locations(all_text):
            iso, country, city, ping = row
            country_disp, city_disp = self._localize_country_city(iso, country, city)
            self.store_all.append([iso, country_disp, city_disp, int(ping), city])
        self.store_all_filtered.refilter()

        # Apply last location selection once, after the first locations load.
        if not self._startup_loc_applied:
            self._startup_loc_applied = True
            try:
                self.cfg = load_config()
                if self.cfg.get("remember_last_location"):
                    loc = (self.cfg.get("last_location") or "").strip()
                    if loc:
                        self._select_location_in_lists(loc)
            except Exception:
                pass

    def on_row_activated(self, tv, path, col):
        """Double-click on a location.

        Behaviour:
        - If VPN is already connected: switch location without asking sudo password.
        - If VPN is disconnected: connect using the same interactive sudo flow as the green "Подключить" button.
        """
        model = tv.get_model()
        it = model.get_iter(path)
        iso, country, city_disp, ping, city_real = model[it]
        loc = (city_real or "").strip() or (iso or "").strip()
        if not loc:
            return

        if getattr(self, "_last_connected", False):
            # When already connected, switching locations works without extra auth.
            self.connect_location(loc)
            return

        # When disconnected, use interactive sudo prompt (same as connect button).
        pwd = self._ask_sudo_password()
        if not pwd:
            return

        def work():
            return cli.connect_location_pw(pwd, loc)

        def ok(_):
            self._kick_status()

        def err(e: Exception):
            self._set_error(str(e))

        run_bg(work, ok, err)

    def _selected_location(self):
        for tv in (self.tv_all, self.tv_fast):
            sel = tv.get_selection()
            model, it = sel.get_selected()
            if it:
                iso, country, city_disp, ping, city_real = model[it]
                return (city_real or "").strip() or (iso or "").strip()
        return ""

    def _select_location_in_lists(self, loc: str):
        target = (loc or "").strip().lower()
        if not target:
            return

        def _select_one(tv: Gtk.TreeView):
            model = tv.get_model()
            it = model.get_iter_first() if model else None
            idx = 0
            while it is not None:
                iso, country, city_disp, ping, city_real = model[it]
                val = ((city_real or iso) or "").strip().lower()
                if val == target:
                    path = Gtk.TreePath(idx)
                    tv.get_selection().select_path(path)
                    tv.scroll_to_cell(path, None, True, 0.5, 0.0)
                    return True
                idx += 1
                it = model.iter_next(it)
            return False

        # Prefer full list, fallback to fast list.
        if not _select_one(self.tv_all):
            _select_one(self.tv_fast)

    def connect_selected(self):
        loc = self._selected_location()
        if not loc:
            self.info("Выбери локацию справа или нажми «Самая быстрая».", err=True)
            return
        pwd = self._ask_sudo_password()
        if not pwd:
            self.info("Отменено", err=True)
            return
        self.info(f"Подключаюсь к {loc}…")
        run_bg(lambda: cli.connect_location_pw(loc, pwd),
               lambda out: (self.info(out or "Подключено"), self.refresh_all()),
               lambda e: self.info(f"Ошибка: {e}", err=True))

    def connect_location(self, loc: str):
        self.info(f"Подключаюсь к {loc}…")
        run_bg(lambda: cli.connect_location(loc),
               lambda out: (self.info(out or "Подключено"), self.refresh_all()),
               lambda e: self.info(f"Ошибка: {e}", err=True))

    def connect_fastest(self):
        self.info("Подключаюсь к самой быстрой…")
        run_bg(lambda: cli.connect_fastest(),
               lambda out: (self.info(out or "Подключено"), self.refresh_all()),
               lambda e: self.info(f"Ошибка: {e}", err=True))

    def disconnect(self):
        pwd = self._ask_sudo_password()
        if not pwd:
            self.info("Отменено", err=True)
            return
        self.info("Отключаю…")
        run_bg(lambda: cli.disconnect_pw(pwd),
               lambda out: (self.info(out or "Отключено"), self.refresh_all()),
               lambda e: self.info(f"Ошибка: {e}", err=True))

    # Exclusions
    def _render_exclusions(self, mode_text: str, show_text: str):
        m = re.search(r"(GENERAL|SELECTIVE)", mode_text or "")
        mode = m.group(1) if m else "GENERAL"
        self.lbl_excl_mode.set_text(f"Режим исключений: {mode}")
        self.cmb_excl_mode.set_active_id(mode)
        self.store_excl.clear()
        for ln in (show_text or "").splitlines():
            ln = ln.strip()
            if not ln or ln.lower().startswith("exclusions for"):
                continue
            self.store_excl.append([ln])

    def excl_apply_mode(self):
        mode = self.cmb_excl_mode.get_active_id() or "GENERAL"
        self.info(f"Ставлю {mode}…")
        run_bg(lambda: cli.exclusions_mode_set(mode),
               lambda out: (self.info(out or "Готово"), self.refresh_all()),
               lambda e: self.info(f"Ошибка: {e}", err=True))

    def excl_add(self):
        s = (self.entry_excl_add.get_text() or "").strip()
        if not s:
            self.info("Введи домен (example.com).", err=True)
            return
        items = [x for x in re.split(r"[\s,;]+", s) if x]
        self.info("Добавляю…")
        run_bg(lambda: cli.exclusions_add(items),
               lambda out: (self.info(out or "Добавлено"), self.refresh_all()),
               lambda e: self.info(f"Ошибка: {e}", err=True))

    def excl_remove_selected(self):
        sel = self.tv_excl.get_selection()
        model, it = sel.get_selected()
        if not it:
            self.info("Выбери домен.", err=True)
            return
        dom = model[it][0]
        self.info("Удаляю…")
        run_bg(lambda: cli.exclusions_remove([dom]),
               lambda out: (self.info(out or "Удалено"), self.refresh_all()),
               lambda e: self.info(f"Ошибка: {e}", err=True))

    def excl_clear(self):
        self.info("Очищаю…")
        run_bg(lambda: cli.exclusions_clear(),
               lambda out: (self.info(out or "Очищено"), self.refresh_all()),
               lambda e: self.info(f"Ошибка: {e}", err=True))

    # Support
    def export_logs(self):
        folder = self.fc_logs_dir.get_filename() or str(Path.home()/"Downloads")
        self.lbl_support_out.set_text("Экспортирую…")
        run_bg(lambda: cli.export_logs(folder),
               lambda out: self.lbl_support_out.set_text(out or f"Готово: {folder}"),
               lambda e: self.lbl_support_out.set_text(f"Ошибка: {e}"))

    # Settings
    def _render_settings(self, cfg_text: str):
        cfg = parse_config(cfg_text)
        self.cmb_mode.set_active_id("socks" if "socks" in (cfg.get("mode","tun").lower()) else "tun")
        dns = cfg.get("dns upstream","")
        self.entry_dns.set_text("" if dns.lower().startswith("default") else dns)
        self.sw_change_dns.set_active(bool_on(cfg.get("change system dns","off")))
        self.sw_crash.set_active(bool_on(cfg.get("crash reporting","off")))
        self.sw_telemetry.set_active(bool_on(cfg.get("send anonymized usage data","off")))
        proto = cfg.get("protocol","auto").lower()
        if "http2" in proto: self.cmb_protocol.set_active_id("http2")
        elif "quic" in proto: self.cmb_protocol.set_active_id("quic")
        else: self.cmb_protocol.set_active_id("auto")
        pq = cfg.get("post-quantum cryptography","").lower()
        self.sw_pq.set_active("on" in pq or pq.strip()=="true")
        ch = cfg.get("update channel","release").lower()
        if "beta" in ch: self.cmb_update_channel.set_active_id("beta")
        elif "nightly" in ch: self.cmb_update_channel.set_active_id("nightly")
        else: self.cmb_update_channel.set_active_id("release")
        self.sw_debug.set_active("on" in cfg.get("debug logging","").lower())
        self.sw_notify.set_active("on" in cfg.get("show notifications","").lower())
        self._set_settings_out("Загружено из CLI.")

    # App-only preferences (persisted in ~/.local/share/adguardvpn-gui/config.json)
    def _load_app_prefs_to_ui(self):
        try:
            self.cfg = load_config()
        except Exception:
            self.cfg = {}

        remember = bool(self.cfg.get("remember_last_location", True))
        self.sw_remember_last_loc.set_active(remember)

        sudo_enabled = bool(self.cfg.get("sudo_password_enabled", False)) and bool((self.cfg.get("sudo_password") or "").strip())
        self.sw_sudo_pwd.set_active(sudo_enabled)
        self.ent_sudo_pwd.set_text(str(self.cfg.get("sudo_password") or ""))
        self.ent_sudo_pwd.set_sensitive(sudo_enabled)

        # Language
        lang = str(self.cfg.get("lang") or "ru")
        if lang not in ("ru","en","de"):
            lang = "ru"
        self.cmb_lang.set_active_id(lang)

        # Window size
        w = int(self.cfg.get("window_width", 600) or 600)
        h = int(self.cfg.get("window_height", 600) or 600)
        w = max(400, min(1600, w))
        h = max(400, min(1600, h))
        if self.scale_win_w:
            self.scale_win_w.set_value(w)
        if self.scale_win_h:
            self.scale_win_h.set_value(h)

    def _save_app_prefs_from_ui(self):
        self.cfg = load_config()
        self.cfg["remember_last_location"] = bool(self.sw_remember_last_loc.get_active())

        pwd = (self.ent_sudo_pwd.get_text() or "").strip()
        enabled = bool(self.sw_sudo_pwd.get_active()) and bool(pwd)
        self.cfg["sudo_password_enabled"] = enabled
        self.cfg["sudo_password"] = pwd if enabled else ""
        self.cfg["lang"] = self.cmb_lang.get_active_id() or "ru"

        # Window size (persisted)
        try:
            w = int(self.scale_win_w.get_value()) if self.scale_win_w else 600
            h = int(self.scale_win_h.get_value()) if self.scale_win_h else 600
        except Exception:
            w, h = 600, 600
        self.cfg["window_width"] = max(400, min(1600, w))
        self.cfg["window_height"] = max(400, min(1600, h))

        save_config(self.cfg)

    def _on_sudo_toggle(self):
        # Enable/disable password field; if user enables but field empty -> keep disabled.
        pwd = (self.ent_sudo_pwd.get_text() or "").strip()
        if self.sw_sudo_pwd.get_active() and not pwd:
            self.ent_sudo_pwd.set_sensitive(True)
            self.ent_sudo_pwd.grab_focus()
            # don't persist "enabled" until we have a password
            self.cfg = load_config()
            self.cfg["sudo_password_enabled"] = False
            save_config(self.cfg)
            return
        self.ent_sudo_pwd.set_sensitive(self.sw_sudo_pwd.get_active())
        self._save_app_prefs_from_ui()

    def _on_sudo_entry_changed(self):
        pwd = (self.ent_sudo_pwd.get_text() or "").strip()
        if not pwd:
            # Empty password => switch must be off.
            if self.sw_sudo_pwd.get_active():
                self.sw_sudo_pwd.set_active(False)
            self.ent_sudo_pwd.set_sensitive(False)
        else:
            if self.sw_sudo_pwd.get_active():
                self.ent_sudo_pwd.set_sensitive(True)
        self._save_app_prefs_from_ui()

    def settings_reload(self):
        self._set_settings_out("Читаю…")
        run_bg(lambda: cli.config_show(),
               lambda out: (self._render_settings(out), self._set_settings_out("Обновлено.")),
               lambda e: self._set_settings_out(f"Ошибка: {e}"))

    def settings_apply(self):
        # Persist app-only preferences (language, window size, last-location, sudo password)
        self._save_app_prefs_from_ui()
        save_config(self.cfg)
        self._apply_language()
        self._apply_window_size()

        mode = self.cmb_mode.get_active_id() or "tun"
        dns = (self.entry_dns.get_text() or "").strip()
        def _apply():
            outs = []
            outs.append(cli.config_set_mode(mode))
            outs.append(cli.config_set_dns(dns if dns else "default"))
            outs.append(cli.config_set_change_system_dns(onoff(self.sw_change_dns.get_active())))
            outs.append(cli.config_set_crash_reporting(onoff(self.sw_crash.get_active())))
            outs.append(cli.config_set_telemetry(onoff(self.sw_telemetry.get_active())))
            outs.append(cli.config_set_protocol(self.cmb_protocol.get_active_id() or "auto"))
            outs.append(cli.config_set_post_quantum(onoff(self.sw_pq.get_active())))
            outs.append(cli.config_set_update_channel(self.cmb_update_channel.get_active_id() or "release"))
            outs.append(cli.config_set_debug_logging(onoff(self.sw_debug.get_active())))
            outs.append(cli.config_set_show_notifications(onoff(self.sw_notify.get_active())))
            return "\n".join([o for o in outs if o])
        self._set_settings_out("Применяю…")
        run_bg(_apply,
               lambda out: (self._set_settings_out(out or "Применено."), self.refresh_all()),
               lambda e: self._set_settings_out(f"Ошибка: {e}"))

    # Stats
    def _tick_stats(self):
        if self.connected_since:
            sec = int(time.time() - self.connected_since)
            self.lbl_stat_session_v.set_text(f"{sec//3600:02d}:{(sec%3600)//60:02d}:{sec%60:02d}")
        else:
            self.lbl_stat_session_v.set_text("—")

        rx, tx = iface_bytes(self.current_iface or "tun0")
        self.lbl_stat_iface_v.set_text(self.current_iface if rx >= 0 else "—")
        self.lbl_stat_in_v.set_text(human_bytes(rx))
        self.lbl_stat_out_v.set_text(human_bytes(tx))

        if rx >= 0 and tx >= 0:
            if self._last_rx is None:
                self._last_rx, self._last_tx = rx, tx
            else:
                drx = max(0, rx - self._last_rx)
                dtx = max(0, tx - self._last_tx)
                self._last_rx, self._last_tx = rx, tx
                if drx or dtx:
                    data = load_stats()
                    day = today_key()
                    cur = data.setdefault("daily", {}).get(day, {"rx":0,"tx":0})
                    cur["rx"] = int(cur.get("rx",0) + drx)
                    cur["tx"] = int(cur.get("tx",0) + dtx)
                    data["daily"][day] = cur
                    save_stats(data)
                    self._render_history(data)
        return True

    def _render_history(self, data: dict):
        daily = data.get("daily",{})
        items = sorted(daily.items(), key=lambda kv: kv[0], reverse=True)[:30]
        self.store_hist.clear()
        for day, v in items:
            self.store_hist.append([day, human_bytes(int(v.get("rx",0))), human_bytes(int(v.get("tx",0)))])

def main():
    # When launched from a terminal, Ctrl+C sends SIGINT to the GUI process.
    # A typical desktop app should not be fragile to this; ignore SIGINT.
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except Exception:
        pass

    # Single-instance guard: if already running, show a warning and exit.
    lock_dir = Path.home() / ".local" / "share" / "adguardvpn-gui"
    try:
        lock_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    lock_path = lock_dir / "app.lock"
    try:
        lock_f = open(lock_path, "w")
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_f.write(str(os.getpid()))
        lock_f.flush()
        # keep handle alive for duration of process
        globals()["_SINGLE_INSTANCE_LOCK"] = lock_f
    except Exception:
        try:
            Gtk.init([])
            dlg = Gtk.MessageDialog(
                parent=None,
                flags=0,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text="AdGuard VPN-GUI уже запущен.",
            )
            dlg.format_secondary_text("Проверь трей: иконка уже работает.")
            dlg.run()
            dlg.destroy()
        except Exception:
            pass
        return

    a = App()
    a.show()
    Gtk.main()

if __name__ == "__main__":
    main()