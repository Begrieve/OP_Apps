#!/bin/bash
# Pull the latest Broker Desk code on PythonAnywhere and install any new requirements.
# Run in a PythonAnywhere Bash console:  bash ~/OP_Apps/deploy/update.sh
# Then press the green "Reload" button on the Web tab.
set -e
cd ~/OP_Apps
git pull
# Install for every Python 3.10+ on the machine, so whichever version the Web tab uses works.
for PY in /usr/bin/python3.1[0-9]; do
  [ -x "$PY" ] || continue
  echo "Installing requirements for $(basename "$PY")..."
  "$PY" -m pip install --user -q -r requirements.txt || echo "  (skipped $(basename "$PY"))"
done
echo
echo "Updated. Now go to the Web tab and press Reload."
