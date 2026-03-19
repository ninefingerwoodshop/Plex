# Plex Media Stack - Windows Service with System Tray Icon
# Runs the dashboard, upgrade watcher, and scheduled health checks as a background service
#
# Usage:
#   python service.py                  # Run with tray icon
#   python service.py --install        # Install as Windows startup task
#   python service.py --uninstall      # Remove from startup
#   python service.py --no-tray        # Run without tray icon (headless)

import sys
import os
import threading
import time
import subprocess
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(SCRIPT_DIR, "service.log")
STARTUP_BAT = os.path.join(SCRIPT_DIR, "plex_health_service.bat")
STARTUP_VBS = os.path.join(SCRIPT_DIR, "plex_health_service.vbs")


def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


class PlexHealthService:
    def __init__(self):
        self.running = True
        self.dashboard_thread = None
        self.watcher_thread = None
        self.scheduler_thread = None
        self.dashboard_port = 5050

    def start_dashboard(self):
        """Start the web dashboard in a thread."""
        log("Starting web dashboard on port 5050...")
        try:
            os.chdir(SCRIPT_DIR)
            from dashboard import app
            app.run(host="0.0.0.0", port=self.dashboard_port, debug=False, use_reloader=False)
        except Exception as e:
            log(f"Dashboard error: {e}")

    def start_watcher(self):
        """Start the upgrade watcher in a thread."""
        log("Starting upgrade watcher (60s intervals)...")
        try:
            os.chdir(SCRIPT_DIR)
            from upgrade_watcher import run_once
            while self.running:
                try:
                    run_once(dry_run=False)
                except Exception as e:
                    log(f"Watcher error: {e}")
                time.sleep(60)
        except Exception as e:
            log(f"Watcher thread error: {e}")

    def start_scheduler(self):
        """Run health checks every 6 hours."""
        log("Starting scheduler (health checks every 6 hours)...")
        time.sleep(30)  # Wait for other services to start
        try:
            os.chdir(SCRIPT_DIR)
            from scheduler import run_health_check
            while self.running:
                try:
                    run_health_check()
                except Exception as e:
                    log(f"Scheduler error: {e}")
                # Sleep 6 hours
                for _ in range(6 * 60):
                    if not self.running:
                        return
                    time.sleep(60)
        except Exception as e:
            log(f"Scheduler thread error: {e}")

    def start_all(self):
        """Start all services in threads."""
        self.dashboard_thread = threading.Thread(target=self.start_dashboard, daemon=True)
        self.watcher_thread = threading.Thread(target=self.start_watcher, daemon=True)
        self.scheduler_thread = threading.Thread(target=self.start_scheduler, daemon=True)

        self.dashboard_thread.start()
        self.watcher_thread.start()
        self.scheduler_thread.start()

        log("All services started!")
        log(f"  Dashboard: http://localhost:{self.dashboard_port}")
        log(f"  Upgrade watcher: active")
        log(f"  Health checks: every 6 hours")

    def stop(self):
        """Stop all services."""
        log("Stopping services...")
        self.running = False


def run_with_tray(service):
    """Run with a system tray icon."""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        log("pystray/pillow not installed. Run: pip install pystray pillow")
        log("Running without tray icon...")
        service.start_all()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            service.stop()
        return

    # Create a simple icon (orange P on dark background)
    def create_icon():
        img = Image.new("RGB", (64, 64), "#1a1a2e")
        draw = ImageDraw.Draw(img)
        # Draw a simple "P" shape
        draw.rectangle([16, 12, 24, 52], fill="#e5a00d")  # vertical bar
        draw.rectangle([16, 12, 44, 20], fill="#e5a00d")  # top bar
        draw.rectangle([36, 12, 44, 32], fill="#e5a00d")  # right bar
        draw.rectangle([16, 24, 44, 32], fill="#e5a00d")  # middle bar
        return img

    def on_open_dashboard(icon, item):
        import webbrowser
        webbrowser.open(f"http://localhost:{service.dashboard_port}")

    def on_run_health(icon, item):
        threading.Thread(target=lambda: _run_health(), daemon=True).start()

    def _run_health():
        os.chdir(SCRIPT_DIR)
        from scheduler import run_health_check
        run_health_check()

    def on_quit(icon, item):
        service.stop()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Open Dashboard", on_open_dashboard, default=True),
        pystray.MenuItem("Run Health Check", on_run_health),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", on_quit),
    )

    icon = pystray.Icon("PlexHealth", create_icon(), "Plex Health Dashboard", menu)

    # Start services before showing icon
    service.start_all()

    log("Tray icon active. Right-click for options.")
    icon.run()


def install_startup():
    """Install as a Windows startup task."""
    python_exe = sys.executable
    script_path = os.path.abspath(__file__)

    # Create a batch file
    with open(STARTUP_BAT, "w") as f:
        f.write(f'@echo off\ncd /d "{SCRIPT_DIR}"\n"{python_exe}" "{script_path}"\n')

    # Create a VBS wrapper to run hidden
    with open(STARTUP_VBS, "w") as f:
        f.write(f'Set WshShell = CreateObject("WScript.Shell")\n')
        f.write(f'WshShell.Run chr(34) & "{STARTUP_BAT}" & chr(34), 0\n')
        f.write(f'Set WshShell = Nothing\n')

    # Add to startup folder
    startup_folder = os.path.join(
        os.environ.get("APPDATA", ""),
        "Microsoft", "Windows", "Start Menu", "Programs", "Startup"
    )

    shortcut_path = os.path.join(startup_folder, "PlexHealthDashboard.vbs")
    try:
        import shutil
        shutil.copy2(STARTUP_VBS, shortcut_path)
        log(f"Installed to Windows startup: {shortcut_path}")
        log("Plex Health Dashboard will start automatically on login.")
    except Exception as e:
        log(f"Could not install to startup: {e}")
        log(f"Manually copy {STARTUP_VBS} to {startup_folder}")


def uninstall_startup():
    """Remove from Windows startup."""
    startup_folder = os.path.join(
        os.environ.get("APPDATA", ""),
        "Microsoft", "Windows", "Start Menu", "Programs", "Startup"
    )
    shortcut_path = os.path.join(startup_folder, "PlexHealthDashboard.vbs")

    if os.path.exists(shortcut_path):
        os.remove(shortcut_path)
        log(f"Removed from startup: {shortcut_path}")
    else:
        log("Not found in startup folder.")

    for f in [STARTUP_BAT, STARTUP_VBS]:
        if os.path.exists(f):
            os.remove(f)


if __name__ == "__main__":
    if "--install" in sys.argv:
        install_startup()
    elif "--uninstall" in sys.argv:
        uninstall_startup()
    elif "--no-tray" in sys.argv:
        service = PlexHealthService()
        service.start_all()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            service.stop()
    else:
        service = PlexHealthService()
        run_with_tray(service)
