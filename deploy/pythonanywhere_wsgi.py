# ---------------------------------------------------------------------------
# Broker Desk - PythonAnywhere WSGI file
#
# Paste this whole file into the WSGI configuration file on the PythonAnywhere
# "Web" tab, then change the two lines marked CHANGE ME.
# ---------------------------------------------------------------------------
import os
import sys

USERNAME = "yourusername"                       # CHANGE ME: your PythonAnywhere username
os.environ["TRACKER_PASSWORD"] = "choose-a-strong-password"   # CHANGE ME: your sign-in password

HOME = f"/home/{USERNAME}"
APP_DIR = f"{HOME}/OP_Apps"                     # where the code was cloned
DATA_DIR = f"{HOME}/broker-data"                # your database lives here, outside the code
os.makedirs(DATA_DIR, exist_ok=True)

os.environ["TRACKER_DB"] = f"{DATA_DIR}/insurance.db"
os.environ["TRACKER_HTTPS"] = "1"
os.environ.setdefault("TRACKER_TZ", "America/Port_of_Spain")

if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from wsgi import application  # noqa: E402,F401
