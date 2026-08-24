#!/usr/bin/env python3
"""
DiskPulse NAS GUI Launcher & Desktop Controller
Provides 4 GUI modes for end-users:
  1. Window: Native Desktop Window (pywebview)
  2. Tray: System Tray App + Browser Auto-launch (pystray)
  3. Hybrid: Native Desktop Window with System Tray Minimize
  4. Control-Panel: Sleek Dark-Themed Desktop Control Panel (CustomTkinter/Tkinter)

Usage:
  python gui_launcher.py                 # Interactive Selector
  python gui_launcher.py --mode window
  python gui_launcher.py --mode tray
  python gui_launcher.py --mode hybrid
  python gui_launcher.py --mode control-panel
"""
import argparse
import io
import multiprocessing
import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

# Safe stream fallback for PyInstaller --windowed / --noconsole mode where stdout/stderr are None
class SafeStream:
    def write(self, data):
        pass
    def flush(self):
        pass
    def isatty(self):
        return False

if sys.stdout is None:
    sys.stdout = SafeStream()
if sys.stderr is None:
    sys.stderr = SafeStream()
if sys.stdin is None:
    sys.stdin = io.StringIO()

# Ensure multiprocessing support for PyInstaller frozen executables
multiprocessing.freeze_support()

# Register bundled vendor binaries (ffmpeg, ffprobe, smartctl) on PATH
# before any backend module checks for them via shutil.which().
from backend.embedded_tools import setup_embedded_tools
setup_embedded_tools()

import backend.main
from backend.config import HOST, PORT, STORAGE_ROOT, BASE_DIR
from backend.icon_utils import create_diskpulse_icon
from backend.server_runner import BackgroundServer


def open_folder(path_str: str):
    """Open a folder in Windows Explorer or default file manager."""
    try:
        Path(path_str).mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(path_str)
        elif sys.platform == "darwin":
            subprocess.run(["open", path_str], check=False)
        else:
            subprocess.run(["xdg-open", path_str], check=False)
    except Exception as e:
        print(f"Could not open folder: {e}")


# ==============================================================================
# MODE 1: NATIVE DESKTOP WINDOW (pywebview)
# ==============================================================================
def run_window_mode(server: BackgroundServer):
    """Launch DiskPulse in a native desktop window powered by pywebview."""
    import webview

    print("[DiskPulse GUI] Starting server in background...")
    server.start()
    if not server.wait_until_ready(timeout=10.0):
        print("[DiskPulse GUI] Warning: Server start timed out, attempting window open anyway...")

    url = server.url

    def on_closing():
        print("[DiskPulse GUI] Desktop window closed. Stopping server...")
        server.stop()

    window = webview.create_window(
        title="DiskPulse NAS Storage Hub",
        url=url,
        width=1280,
        height=820,
        min_size=(960, 600),
        background_color="#0f172a",
        text_select=True,
    )
    window.events.closing += on_closing

    try:
        webview.start(private_mode=False)
    finally:
        server.stop()


# ==============================================================================
# MODE 2: SYSTEM TRAY APP (pystray)
# ==============================================================================
def run_tray_mode(server: BackgroundServer):
    """Launch DiskPulse as a background System Tray service and auto-open browser."""
    import pystray
    from pystray import MenuItem as item, Menu

    print("[DiskPulse GUI] Starting server in background...")
    server.start()
    server.wait_until_ready(timeout=10.0)

    url = server.url
    print(f"[DiskPulse GUI] Opening web browser at {url}...")
    webbrowser.open(url)

    icon_img = create_diskpulse_icon(64)

    def on_open_dashboard(icon, _item):
        webbrowser.open(url)

    def on_open_storage(icon, _item):
        open_folder(STORAGE_ROOT)

    def on_open_speedtest(icon, _item):
        webbrowser.open(f"{url}#speedtest")

    def on_exit(icon, _item):
        print("[DiskPulse GUI] Stopping from system tray...")
        icon.stop()
        server.stop()

    menu = Menu(
        item(f"DiskPulse NAS (Port {server.port})", None, enabled=False),
        Menu.SEPARATOR,
        item("🌐 Open Web Dashboard", on_open_dashboard, default=True),
        item("📁 Open Storage Pool Folder", on_open_storage),
        item("⚡ Speed Test & Diagnostics", on_open_speedtest),
        Menu.SEPARATOR,
        item("❌ Exit DiskPulse", on_exit),
    )

    tray_icon = pystray.Icon(
        name="DiskPulseNAS",
        icon=icon_img,
        title=f"DiskPulse NAS Hub (Online - Port {server.port})",
        menu=menu,
    )

    print("[DiskPulse GUI] System Tray active. Right-click the icon in your taskbar near the clock.")
    try:
        tray_icon.run()
    finally:
        server.stop()


# ==============================================================================
# MODE 3: HYBRID (NATIVE WINDOW + SYSTEM TRAY) - DEFAULT
# ==============================================================================
def run_hybrid_mode(server: BackgroundServer):
    """Native WebView window with System Tray integration (minimize-to-tray)."""
    import webview
    import pystray
    from pystray import MenuItem as item, Menu

    print("[DiskPulse GUI] Starting server in background...")
    server.start()
    server.wait_until_ready(timeout=10.0)

    url = server.url
    icon_img = create_diskpulse_icon(64)
    tray_holder = {"icon": None}
    window_holder = {"window": None, "closed": False}

    def on_show_window(icon=None, _item=None):
        w = window_holder.get("window")
        if w:
            try:
                w.show()
                w.restore()
            except Exception:
                pass

    def on_open_browser(icon=None, _item=None):
        webbrowser.open(url)

    def on_open_storage(icon=None, _item=None):
        open_folder(STORAGE_ROOT)

    def on_open_speedtest(icon=None, _item=None):
        webbrowser.open(f"{url}#speedtest")

    def on_hybrid_exit(icon=None, _item=None):
        print("[DiskPulse GUI] Exiting DiskPulse...")
        window_holder["closed"] = True
        if icon:
            try:
                icon.stop()
            except Exception:
                pass
        w = window_holder.get("window")
        if w:
            try:
                w.destroy()
            except Exception:
                pass
        server.stop()
        os._exit(0)

    menu = Menu(
        item(f"⚡ DiskPulse NAS (Port {server.port})", None, enabled=False),
        Menu.SEPARATOR,
        item("🖥️ Show Desktop Window", on_show_window, default=True),
        item("🌐 Open in Web Browser", on_open_browser),
        item("📁 Open Storage Pool Folder", on_open_storage),
        item("⚡ Speed Test & Diagnostics", on_open_speedtest),
        Menu.SEPARATOR,
        item("❌ Exit DiskPulse", on_hybrid_exit),
    )

    tray_icon = pystray.Icon(
        name="DiskPulseHybrid",
        icon=icon_img,
        title=f"DiskPulse NAS Storage Hub (Online - Port {server.port})",
        menu=menu,
    )
    tray_holder["icon"] = tray_icon

    # Start system tray in background daemon thread
    tray_thread = threading.Thread(target=tray_icon.run, daemon=True)
    tray_thread.start()

    def on_window_closing():
        if not window_holder["closed"]:
            # Minimize/Hide to system tray instead of killing the NAS background services
            print("[DiskPulse GUI] Window minimized to system tray.")
            try:
                w = window_holder.get("window")
                if w:
                    w.hide()
                return False
            except Exception:
                pass

    window = webview.create_window(
        title="DiskPulse NAS Storage Hub",
        url=url,
        width=1280,
        height=820,
        min_size=(960, 600),
        background_color="#0f172a",
        text_select=True,
    )
    window_holder["window"] = window
    window.events.closing += on_window_closing

    try:
        webview.start(private_mode=False)
    finally:
        if not window_holder["closed"]:
            on_hybrid_exit(tray_holder.get("icon"))


# ==============================================================================
# MODE 4: SLEEK DESKTOP CONTROL PANEL (CustomTkinter / Modern Tkinter)
# ==============================================================================
def run_control_panel_mode(server: BackgroundServer):
    """Launch a modern dark-themed Desktop Control Panel GUI."""
    try:
        import customtkinter as ctk
        _use_ctk = True
    except ImportError:
        _use_ctk = False
        import tkinter as tk
        from tkinter import ttk, messagebox

    if _use_ctk:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        root = ctk.CTk()
    else:
        root = tk.Tk()

    root.title("DiskPulse NAS Control Panel")
    root.geometry("640x520")
    root.minsize(560, 480)

    # Set background color
    bg_color = "#0f172a"
    card_color = "#1e293b"
    accent_cyan = "#38bdf8"
    accent_emerald = "#10b981"
    accent_rose = "#f43f5e"

    if _use_ctk:
        root.configure(fg_color=bg_color)
    else:
        root.configure(bg=bg_color)

    # Start server initially
    print("[DiskPulse GUI] Starting server...")
    server.start()

    # Layout container
    if _use_ctk:
        main_frame = ctk.CTkFrame(root, fg_color="transparent")
        main_frame.pack(fill="both", expand=True, padx=24, pady=20)

        # Title Card
        title_box = ctk.CTkFrame(main_frame, fg_color=card_color, corner_radius=12)
        title_box.pack(fill="x", pady=(0, 16), ipady=8, ipadx=12)

        lbl_title = ctk.CTkLabel(
            title_box,
            text="⚡ DiskPulse NAS Storage Hub",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color="#f8fafc",
        )
        lbl_title.pack(anchor="w", padx=12, pady=(4, 0))

        lbl_subtitle = ctk.CTkLabel(
            title_box,
            text="Desktop Server Controller & Diagnostics",
            font=ctk.CTkFont(size=12),
            text_color="#94a3b8",
        )
        lbl_subtitle.pack(anchor="w", padx=12, pady=(0, 4))

        # Status Card
        status_box = ctk.CTkFrame(main_frame, fg_color=card_color, corner_radius=12)
        status_box.pack(fill="x", pady=(0, 16), padx=0, ipady=10, ipadx=12)

        lbl_status = ctk.CTkLabel(
            status_box,
            text="● Server Status: Online",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=accent_emerald,
        )
        lbl_status.pack(anchor="w", padx=12, pady=(4, 2))

        lbl_url = ctk.CTkLabel(
            status_box,
            text=f"Local URL: {server.url}  |  Port: {server.port}",
            font=ctk.CTkFont(size=12),
            text_color="#cbd5e1",
        )
        lbl_url.pack(anchor="w", padx=12, pady=(0, 2))

        lbl_storage = ctk.CTkLabel(
            status_box,
            text=f"Storage Pool: {STORAGE_ROOT}",
            font=ctk.CTkFont(size=11),
            text_color="#64748b",
        )
        lbl_storage.pack(anchor="w", padx=12, pady=(0, 4))

        # Actions Card
        actions_box = ctk.CTkFrame(main_frame, fg_color=card_color, corner_radius=12)
        actions_box.pack(fill="both", expand=True, pady=(0, 16), ipady=12, ipadx=12)

        btn_dashboard = ctk.CTkButton(
            actions_box,
            text="🌐 Open Web Dashboard",
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#0284c7",
            hover_color="#0369a1",
            height=42,
            corner_radius=8,
            command=lambda: webbrowser.open(server.url),
        )
        btn_dashboard.pack(fill="x", padx=16, pady=(10, 8))

        btn_storage = ctk.CTkButton(
            actions_box,
            text="📁 Open Storage Pool Folder",
            font=ctk.CTkFont(size=13),
            fg_color="#334155",
            hover_color="#475569",
            height=38,
            corner_radius=8,
            command=lambda: open_folder(STORAGE_ROOT),
        )
        btn_storage.pack(fill="x", padx=16, pady=4)

        btn_speedtest = ctk.CTkButton(
            actions_box,
            text="⚡ Run Speed Test & S.M.A.R.T. Health",
            font=ctk.CTkFont(size=13),
            fg_color="#334155",
            hover_color="#475569",
            height=38,
            corner_radius=8,
            command=lambda: webbrowser.open(f"{server.url}#speedtest"),
        )
        btn_speedtest.pack(fill="x", padx=16, pady=(4, 10))

        # Bottom Bar: Toggle & Exit
        bottom_bar = ctk.CTkFrame(main_frame, fg_color="transparent")
        bottom_bar.pack(fill="x", pady=0)

        def toggle_server():
            if server._is_running:
                server.stop()
                lbl_status.configure(text="● Server Status: Stopped", text_color=accent_rose)
                btn_toggle.configure(text="▶ Start Server", fg_color=accent_emerald, hover_color="#059669")
                btn_dashboard.configure(state="disabled")
            else:
                server.start()
                lbl_status.configure(text="● Server Status: Online", text_color=accent_emerald)
                btn_toggle.configure(text="⏹ Stop Server", fg_color="#b91c1c", hover_color="#991b1b")
                btn_dashboard.configure(state="normal")

        btn_toggle = ctk.CTkButton(
            bottom_bar,
            text="⏹ Stop Server",
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#b91c1c",
            hover_color="#991b1b",
            height=36,
            corner_radius=8,
            command=toggle_server,
        )
        btn_toggle.pack(side="left", fill="x", expand=True, padx=(0, 8))

        def quit_app():
            server.stop()
            root.destroy()

        btn_quit = ctk.CTkButton(
            bottom_bar,
            text="❌ Exit Application",
            font=ctk.CTkFont(size=13),
            fg_color="#1e293b",
            hover_color="#334155",
            height=36,
            corner_radius=8,
            command=quit_app,
        )
        btn_quit.pack(side="right", fill="x", expand=True, padx=(8, 0))

    else:
        # Standard Tkinter Fallback
        lbl_title = tk.Label(root, text="⚡ DiskPulse NAS Hub", font=("Arial", 16, "bold"), fg="#f8fafc", bg=bg_color)
        lbl_title.pack(pady=(16, 4))

        lbl_status = tk.Label(root, text="● Server Status: Online", font=("Arial", 12, "bold"), fg=accent_emerald, bg=bg_color)
        lbl_status.pack(pady=4)

        btn_dash = tk.Button(root, text="Open Web Dashboard", bg="#0284c7", fg="white", font=("Arial", 12), command=lambda: webbrowser.open(server.url))
        btn_dash.pack(fill="x", padx=40, pady=8)

        btn_folder = tk.Button(root, text="Open Storage Folder", bg="#334155", fg="white", font=("Arial", 11), command=lambda: open_folder(STORAGE_ROOT))
        btn_folder.pack(fill="x", padx=40, pady=8)

        def quit_app():
            server.stop()
            root.destroy()

        btn_exit = tk.Button(root, text="Exit DiskPulse", bg="#b91c1c", fg="white", font=("Arial", 11), command=quit_app)
        btn_exit.pack(fill="x", padx=40, pady=16)

    root.protocol("WM_DELETE_WINDOW", lambda: (server.stop(), root.destroy()))
    root.mainloop()


# ==============================================================================
# INTERACTIVE SELECTOR DIALOG
# ==============================================================================
def run_interactive_selector(server: BackgroundServer):
    """Show a friendly mode chooser window so the user can try each option."""
    try:
        import customtkinter as ctk
        _use_ctk = True
    except ImportError:
        _use_ctk = False
        import tkinter as tk

    chosen_mode = {"mode": "window"}

    if _use_ctk:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        picker = ctk.CTk()
    else:
        picker = tk.Tk()

    picker.title("DiskPulse NAS - Select GUI Mode")
    picker.geometry("560x520")
    picker.minsize(500, 460)

    bg_color = "#0f172a"
    card_color = "#1e293b"

    if _use_ctk:
        picker.configure(fg_color=bg_color)
        frame = ctk.CTkFrame(picker, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=24, pady=20)

        lbl = ctk.CTkLabel(
            frame,
            text="⚡ DiskPulse NAS GUI Selector",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="#f8fafc",
        )
        lbl.pack(anchor="w", pady=(0, 4))

        sub = ctk.CTkLabel(
            frame,
            text="Choose which interface experience you would like to test:",
            font=ctk.CTkFont(size=12),
            text_color="#94a3b8",
        )
        sub.pack(anchor="w", pady=(0, 16))

        modes = [
            ("1. Native Desktop Window (pywebview)", "window", "Runs DiskPulse in a dedicated desktop window without a web browser."),
            ("2. System Tray App (pystray)", "tray", "Runs silently in Windows taskbar tray & auto-opens your default browser."),
            ("3. Hybrid Window + System Tray", "hybrid", "Dedicated desktop window with minimize-to-tray background support."),
            ("4. Desktop Control Panel", "control-panel", "Modern dark control panel with status, quick action buttons & logs."),
        ]

        def select_and_launch(m):
            chosen_mode["mode"] = m
            picker.destroy()

        for title, mode_key, desc in modes:
            card = ctk.CTkFrame(frame, fg_color=card_color, corner_radius=10)
            card.pack(fill="x", pady=6, ipady=4, ipadx=8)

            btn = ctk.CTkButton(
                card,
                text=title,
                font=ctk.CTkFont(size=13, weight="bold"),
                fg_color="#0284c7" if mode_key == "window" else "#334155",
                hover_color="#0369a1",
                height=34,
                corner_radius=6,
                command=lambda m=mode_key: select_and_launch(m),
            )
            btn.pack(fill="x", padx=10, pady=(8, 2))

            desc_lbl = ctk.CTkLabel(card, text=desc, font=ctk.CTkFont(size=11), text_color="#94a3b8")
            desc_lbl.pack(anchor="w", padx=12, pady=(0, 6))

    picker.mainloop()

    # Launch chosen mode
    mode = chosen_mode["mode"]
    print(f"[DiskPulse GUI] Launching selected mode: {mode}")
    if mode == "window":
        run_window_mode(server)
    elif mode == "tray":
        run_tray_mode(server)
    elif mode == "hybrid":
        run_hybrid_mode(server)
    elif mode == "control-panel":
        run_control_panel_mode(server)


# ==============================================================================
# MAIN ENTRYPOINT
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(description="DiskPulse NAS GUI Launcher")
    parser.add_argument(
        "--mode",
        choices=["window", "tray", "hybrid", "control-panel", "picker"],
        default="hybrid",
        help="GUI mode to launch (default: hybrid window + system tray)",
    )
    args = parser.parse_args()

    server = BackgroundServer()

    if args.mode == "window":
        run_window_mode(server)
    elif args.mode == "tray":
        run_tray_mode(server)
    elif args.mode == "control-panel":
        run_control_panel_mode(server)
    elif args.mode == "picker":
        run_interactive_selector(server)
    else:
        run_hybrid_mode(server)


if __name__ == "__main__":
    main()
