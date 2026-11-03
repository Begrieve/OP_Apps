"""Entry point for web hosts (PythonAnywhere, Render, gunicorn, etc.).

Set these environment variables on the host:
  TRACKER_PASSWORD  - the password to sign in (required when online)
  TRACKER_DB        - full path to the database file on a persistent disk
  TRACKER_HTTPS=1   - when the site is served over https
"""
import os

from insurance_tracker.app import create_app

if not os.environ.get("TRACKER_PASSWORD"):
    raise RuntimeError("Set TRACKER_PASSWORD before putting Broker Desk online.")

application = app = create_app()
