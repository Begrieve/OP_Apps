@echo off
title Broker Desk
cd /d "%~dp0"

rem ---- Find Python ----------------------------------------------------------
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not exist ".venv\Scripts\python.exe" if not defined PY goto nopython

rem ---- First start: create a private Python environment for Broker Desk ------
if not exist ".venv\Scripts\python.exe" (
  echo.
  echo  First start: setting up Broker Desk. This takes a minute or two...
  %PY% -m venv .venv || goto failed
)

rem ---- Install / update what Broker Desk needs (only when it changes) --------
fc /b requirements.txt ".venv\installed-requirements.txt" >nul 2>nul
if errorlevel 1 (
  echo  Installing the parts Broker Desk needs...
  set "OK="
  if exist "wheels\" ".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check --no-index --find-links=wheels -r requirements.txt >nul 2>nul && set "OK=1"
  if not defined OK ".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt || goto failed
  copy /y requirements.txt ".venv\installed-requirements.txt" >nul
)

rem ---- Run -------------------------------------------------------------------
".venv\Scripts\python.exe" start_broker_desk.py
if errorlevel 1 goto failed
goto end

:nopython
echo.
echo  Python is not installed on this computer.
echo  1. Go to https://www.python.org/downloads/ and install Python.
echo  2. On the first screen of the installer, tick "Add python.exe to PATH".
echo  3. Then double-click "Start Broker Desk" again.
echo.
pause
goto end

:failed
echo.
echo  Something went wrong starting Broker Desk (see the message above).
echo  The first start needs an internet connection. If this keeps happening,
echo  delete the ".venv" folder here and try again.
echo.
pause

:end
