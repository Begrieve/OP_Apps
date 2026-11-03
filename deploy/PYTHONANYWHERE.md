# Putting Broker Desk online with PythonAnywhere (free)

When you're done, Broker Desk is at `https://yourusername.pythonanywhere.com`, protected by
your password. Use it from any computer or phone. Nothing to download.

You only do this once. It takes about 15 minutes.

## 1. Create the account
1. Go to https://www.pythonanywhere.com and click **Pricing & signup**, then **Create a Beginner account** (free).
2. Pick your username carefully. It becomes your web address, e.g. `vbsingh.pythonanywhere.com`.
3. Confirm your email.

## 2. Copy the code onto PythonAnywhere
1. On the PythonAnywhere dashboard, under **New console**, click **Bash**.
2. Paste this line and press Enter (copies the app from GitHub):

```bash
git clone -b claude/kind-planck-ofojje https://github.com/Begrieve/OP_Apps.git ~/OP_Apps
```

3. Then paste this line and press Enter (installs what the app needs):

```bash
bash ~/OP_Apps/deploy/update.sh
```

## 3. Create the website
1. Click **Web** in the top menu, then **Add a new web app**, then **Next**.
2. Choose **Manual configuration** (not "Flask"). Then pick the **highest Python version** offered
   (3.10 or newer).
3. Click **Next** until it's created.
4. On the Web tab, find **Code: WSGI configuration file** and click the link
   (`/var/www/yourusername_pythonanywhere_com_wsgi.py`).
5. **Delete everything** in that file and paste in the contents of
   [`deploy/pythonanywhere_wsgi.py`](pythonanywhere_wsgi.py).
6. Change the two **CHANGE ME** lines:
   - `USERNAME = "..."`: your PythonAnywhere username
   - `TRACKER_PASSWORD = "..."`: the password you'll sign in with (make it long, not your email password)
7. Click **Save**.
8. Back on the **Web** tab:
   - Under **Static files**, add URL `/static/` with directory
     `/home/yourusername/OP_Apps/insurance_tracker/static`
   - Under **Security**, switch **Force HTTPS** on.
9. Press the big green **Reload** button at the top.

Open `https://yourusername.pythonanywhere.com`, sign in, and go to **Import** to upload your
renewal listing and commission statement.

## Keeping it running (free plan)
PythonAnywhere emails you about once every 3 months. Log in and click
**"Run until 3 months from today"** on the Web tab, or the site pauses. Your data is not lost.

## Getting updates
When new features are added to the app, open a **Bash** console and run:

```bash
bash ~/OP_Apps/deploy/update.sh
```

Then press **Reload** on the Web tab. Your data is in `~/broker-data/` and is never touched by updates.

## Backups
Click **Import → Download backup** in the app now and then, and keep the file safe.
You can also download `~/broker-data/insurance.db` from PythonAnywhere's **Files** tab.

## If something goes wrong
- **"Something went wrong :-("** in the browser: on the Web tab, open the **Error log** (bottom of
  the page). The last lines say what's wrong. Most often it's a typo in the username in the WSGI file.
- **Forgot the password:** edit the WSGI file, set a new `TRACKER_PASSWORD`, Save, and Reload.
- **Changed the password:** everyone signed in is signed out and has to use the new one.
