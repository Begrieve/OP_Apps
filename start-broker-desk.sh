#!/bin/bash
# Start Broker Desk (Mac: double-click; Linux: run ./start-broker-desk.sh)
cd "$(dirname "$0")" || exit 1

PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    PY="$c"; break
  fi
done

if [ ! -x .venv/bin/python ]; then
  if [ -z "$PY" ]; then
    echo
    echo " Python 3.10 or newer is not installed."
    echo " Install it from https://www.python.org/downloads/ and then start Broker Desk again."
    echo
    read -r -p " Press Enter to close..." _
    exit 1
  fi
  echo
  echo " First start: setting up Broker Desk. This takes a minute or two..."
  "$PY" -m venv .venv || { echo " Could not set up Python (on Linux you may need: sudo apt install python3-venv)"; read -r -p " Press Enter to close..." _; exit 1; }
fi

if ! cmp -s requirements.txt .venv/installed-requirements.txt; then
  echo " Installing the parts Broker Desk needs..."
  { [ -d wheels ] && .venv/bin/python -m pip install -q --disable-pip-version-check --no-index --find-links=wheels -r requirements.txt 2>/dev/null; } \
    || .venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt || { echo " Install failed. The first start needs an internet connection."; read -r -p " Press Enter to close..." _; exit 1; }
  cp requirements.txt .venv/installed-requirements.txt
fi

exec .venv/bin/python start_broker_desk.py
