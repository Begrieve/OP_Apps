"""Run Broker Desk on this computer (and, optionally, for your whole office network).

Started by the "Start Broker Desk" files; you don't normally run this directly.

  * Your data lives in the  data/  folder next to this file (or BROKERDESK_DATA).
  * A copy of the database is saved in  data/backups/  once a day (the last 60 are kept).
  * Settings are in  data/config.json  (created on first start):
        "port": 8080            - the web address port
        "network": true         - true = other computers/phones on your network can connect
                                  false = only this computer
        "timezone": "America/Port_of_Spain"
        "open_browser": true

Forgot the password? Put an empty file named  reset-password.txt  in the data folder and
start Broker Desk again: you'll be asked to create a new password on this computer.
"""
import json
import os
import socket
import sqlite3
import sys
import threading
import time
import webbrowser
from datetime import date

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("BROKERDESK_DATA") or os.path.join(BASE, "data")
DEFAULTS = {"port": 8080, "network": True, "timezone": "America/Port_of_Spain", "open_browser": True}
KEEP_BACKUPS = 60


def load_config():
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, "config.json")
    cfg = dict(DEFAULTS)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except (OSError, ValueError) as exc:
            print(f"  ! Could not read {path} ({exc}); using default settings.")
    else:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(DEFAULTS, f, indent=2)
    return cfg


def daily_backup(db_path):
    """Save one consistent copy of the database per day."""
    if not os.path.exists(db_path):
        return None
    folder = os.path.join(DATA, "backups")
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, f"insurance-{date.today().isoformat()}.db")
    if not os.path.exists(target):
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(target)
        src.backup(dst)
        dst.close()
        src.close()
    daily = sorted(f for f in os.listdir(folder) if f.startswith("insurance-") and f.endswith(".db"))
    for old in daily[:-KEEP_BACKUPS]:
        try:
            os.remove(os.path.join(folder, old))
        except OSError:
            pass
    return target


def lan_address():
    """Best guess at this computer's address on the local network."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))   # no traffic is sent
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def port_free(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def main():
    cfg = load_config()
    db_path = os.path.join(DATA, "insurance.db")
    os.environ["TRACKER_DB"] = db_path
    os.environ["TRACKER_TZ"] = str(cfg.get("timezone") or DEFAULTS["timezone"])
    os.environ["TRACKER_DESKTOP"] = "1"
    os.environ.pop("TRACKER_HTTPS", None)

    host = "0.0.0.0" if cfg.get("network", True) else "127.0.0.1"
    port = int(cfg.get("port") or 8080)
    local_url = f"http://localhost:{port}"

    if not port_free(host, port):
        # Most likely Broker Desk is already running: just open it.
        print(f"\n  Broker Desk already seems to be running at {local_url}")
        print("  (or another program is using that port: change \"port\" in data/config.json).")
        if cfg.get("open_browser", True):
            webbrowser.open(local_url)
        time.sleep(4)
        return

    sys.path.insert(0, BASE)
    from insurance_tracker.app import create_app
    app = create_app()
    backup = daily_backup(db_path)
    reset_flag = os.path.join(DATA, "reset-password.txt")
    if os.path.exists(reset_flag):
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM settings WHERE key = 'password_hash'")
        conn.commit()
        conn.close()
        os.remove(reset_flag)
        print("\n  Password removed. Create a new one in the browser on THIS computer.")

    print("\n  ==============================================")
    print("   Broker Desk is running")
    print("  ==============================================")
    print(f"   On this computer:  {local_url}")
    if host == "0.0.0.0":
        ip = lan_address()
        if ip:
            print(f"   On other devices:  http://{ip}:{port}")
            print("   (same Wi-Fi / office network)")
    print(f"\n   Your data:   {DATA}")
    if backup:
        print(f"   Today's backup: {os.path.basename(backup)}")
    print("\n   Keep this window open while you use Broker Desk.")
    print("   To stop it, close this window (or press Ctrl+C).\n")

    if cfg.get("open_browser", True):
        threading.Timer(1.5, lambda: webbrowser.open(local_url)).start()

    try:
        from waitress import serve
    except ImportError:
        print("  ! The 'waitress' web server isn't installed; using the basic one instead.")
        app.run(host=host, port=port, debug=False)
        return
    serve(app, host=host, port=port, threads=8, ident="BrokerDesk")


if __name__ == "__main__":
    main()
