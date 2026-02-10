# Copyright (c) 2026 SubBotIn <markotdel@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations
import threading, time, re
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
from gi.repository import Gtk, GLib, Gdk

from . import cli
from .tray import Tray
from .utils import human_bytes
from .state import load_stats, save_stats, today_key

UI = Path(__file__).resolve().parent / "ui" / "main_window.ui"
CSS = Path(__file__).resolve().parent / "ui" / "style.css"
BG = Path(__file__).resolve().parent / "ui" / "assets" / "home_bg.svg"
ICON = str((Path(__file__).resolve().parent / "ui" / "icons" / "adguardvpn.svg"))

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
        self.b = Gtk.Builder.new_from_file(str(UI))
        self.win: Gtk.Window = self.b.get_object("main_window")
        self.win.set_default_size(960, 620)
        self.win.set_resizable(True)
        self.win.connect("delete-event", self.on_close_to_tray)

        self.notebook: Gtk.Notebook = self.b.get_object("notebook")
        self.btn_refresh_all: Gtk.Button = self.b.get_object("btn_refresh_all")

        # Home
        self.img_home_bg: Gtk.Image = self.b.get_object("img_home_bg")
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
        self.btn_settings_reload: Gtk.Button = self.b.get_object("btn_settings_reload")
        self.btn_settings_apply: Gtk.Button = self.b.get_object("btn_settings_apply")
        self.lbl_settings_out: Gtk.Label = self.b.get_object("lbl_settings_out")

        self._apply_css()
        self._apply_home_styles()
        self._setup_models()

        # Signals
        self.btn_refresh_all.connect("clicked", lambda *_: self.refresh_all())
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

        # Tray
        self.tray = Tray(ICON, self.show, self.connect_fastest, self.disconnect, self.quit)

        self.current_iface = "tun0"
        self.connected_since = None
        self._last_rx = None
        self._last_tx = None
        GLib.timeout_add(1000, self._tick_stats)

        self.refresh_all()

    def _apply_css(self):
        prov = Gtk.CssProvider()
        prov.load_from_path(str(CSS))
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _apply_home_styles(self):
        # Set bg image
        self.img_home_bg.set_from_file(str(BG))

        # Named styles
        self.b.get_object("home_left_panel").set_name("home_left_panel")
        self.home_title.set_name("home_title_connected")
        self.home_sub.set_name("home_subtitle")

        # Buttons: primary (white) for Disconnect like in screenshot, secondary for Connect (transparent)
        self.btn_disconnect.set_name("primary_btn")
        self.btn_connect.set_name("secondary_btn")

    def _setup_models(self):
        # Locations
        self.store_fast = Gtk.ListStore(str,str,str,int)
        self.store_all = Gtk.ListStore(str,str,str,int)
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
        iso, country, city, ping = model[it]
        return q in (iso or "").lower() or q in (country or "").lower() or q in (city or "").lower()

    def info(self, msg: str, err=False):
        self.lbl_info.set_text(msg)
        self.infobar.set_message_type(Gtk.MessageType.ERROR if err else Gtk.MessageType.INFO)
        self.infobar.set_visible(True)
        GLib.timeout_add(3500, self._hide_infobar)

    def _hide_infobar(self):
        self.infobar.set_visible(False)
        return False

    def show(self):
        self.win.show_all()
        self.win.present()

    def quit(self):
        Gtk.main_quit()

    def on_close_to_tray(self, *_):
        self.win.hide()
        return True

    def refresh_all(self):
        self.info("Обновляю…")
        run_bg(lambda: (cli.status(), cli.list_locations(10), cli.list_locations(), cli.exclusions_mode_get(), cli.exclusions_show(), cli.config_show()),
               self._on_refresh_ok,
               self._on_refresh_err)

    def _on_refresh_ok(self, res):
        st_text, fast_text, all_text, mode_text, excl_text, cfg_text = res
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
        if st.connected:
            self.home_title.set_text("Подключён")
            self.home_sub.set_text(raw)
            self.current_iface = st.iface or self.current_iface
            # Try to show ping for current city from fast table
            ping = self._ping_for_location(st.location, fast_text)
            self.lbl_current_location.set_text(st.location if st.location else "—")
            self.lbl_current_ping.set_text(f"{ping} мс" if ping else "—")
            if self.connected_since is None:
                self.connected_since = time.time()
        else:
            self.home_title.set_text("Отключён")
            self.home_sub.set_text(raw or "VPN отключён")
            self.lbl_current_location.set_text("—")
            self.lbl_current_ping.set_text("—")
            self.connected_since = None

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
            self.store_fast.append(list(row))
        for row in cli.parse_locations(all_text):
            self.store_all.append(list(row))
        self.store_all_filtered.refilter()

    def on_row_activated(self, tv, path, col):
        model = tv.get_model()
        it = model.get_iter(path)
        iso,country,city,ping = model[it]
        self.connect_location(city or iso)

    def _selected_location(self):
        for tv in (self.tv_all, self.tv_fast):
            sel = tv.get_selection()
            model, it = sel.get_selected()
            if it:
                iso,country,city,ping = model[it]
                return city or iso
        return ""

    def connect_selected(self):
        loc = self._selected_location()
        if not loc:
            self.info("Выбери локацию справа или нажми «Самая быстрая».", err=True)
            return
        self.connect_location(loc)

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
        self.info("Отключаю…")
        run_bg(lambda: cli.disconnect(),
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
        self.lbl_settings_out.set_text("Загружено из CLI.")

    def settings_reload(self):
        self.lbl_settings_out.set_text("Читаю…")
        run_bg(lambda: cli.config_show(),
               lambda out: (self._render_settings(out), self.lbl_settings_out.set_text("Обновлено.")),
               lambda e: self.lbl_settings_out.set_text(f"Ошибка: {e}"))

    def settings_apply(self):
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
        self.lbl_settings_out.set_text("Применяю…")
        run_bg(_apply,
               lambda out: (self.lbl_settings_out.set_text(out or "Применено."), self.refresh_all()),
               lambda e: self.lbl_settings_out.set_text(f"Ошибка: {e}"))

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
    a = App()
    a.show()
    Gtk.main()

if __name__ == "__main__":
    main()
