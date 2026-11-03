"""Insurance Broker Tracker: clients, policies, payments, reminders and a meeting calendar."""
import calendar
import os
from datetime import date, datetime, timedelta, timezone

import hashlib
import shutil
import traceback
import hmac
import sqlite3
import tempfile
import time

from flask import (Flask, Response, abort, flash, redirect, render_template,
                   request, session, url_for)
from werkzeug.utils import secure_filename

from . import db, importer, reports, tracker
from .db import execute, query

# ---------------------------------------------------------------------------
# Choice lists
# ---------------------------------------------------------------------------
POLICY_TYPES = [
    "Motor - Private Car Comprehensive", "Motor - Private Car Third Party", "Motor - Private Car",
    "Motor - Commercial Comprehensive", "Motor - Commercial Third Party",
    "Fire - Homeowners / Comprehensive Building", "Fire - Commercial", "Public Liability",
    "Auto", "Homeowners", "Renters", "Condo", "Life - Term", "Life - Whole",
    "Life - Universal", "Health", "Medicare Supplement", "Medicare Advantage",
    "Dental", "Vision", "Disability", "Long-Term Care", "Umbrella",
    "Motorcycle", "Boat / Watercraft", "RV", "Flood", "Earthquake",
    "Commercial Auto", "General Liability", "Commercial Property",
    "Business Owners (BOP)", "Workers' Compensation", "Professional Liability (E&O)",
    "Cyber Liability", "Pet", "Travel", "Annuity", "Other",
]
POLICY_STATUSES = ["Active", "Pending", "Quoted", "Lapsed", "Cancelled", "Expired"]
FREQUENCIES = ["Monthly", "Quarterly", "Semi-Annual", "Annual", "Paid in Full"]
FREQ_MONTHS = {"Monthly": 1, "Quarterly": 3, "Semi-Annual": 6, "Annual": 12}
PAYMENT_METHODS = ["", "Auto-Pay / EFT", "Credit Card", "Check", "Cash",
                   "Direct Bill (Carrier)", "Agency Bill", "Payroll Deduction", "Other"]
MEETING_TYPES = ["Client Meeting", "Policy Review", "Renewal Review", "New Quote",
                 "Claim Assistance", "Phone Call", "Video Call", "Internal / Team",
                 "Carrier / Underwriter", "Personal", "Other"]
CONTACT_PREFS = ["", "Phone", "Text", "Email", "Mail"]

DUE_SOON_DAYS = 14
RENEWAL_WINDOW_DAYS = 60


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------
def parse_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def add_months(d, months):
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return date(y, m, day)


def today():
    """Today's date in the broker's time zone (servers usually run on UTC)."""
    override = os.environ.get("TRACKER_TODAY")  # used by tests
    if override:
        return parse_date(override)
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(os.environ.get("TRACKER_TZ", "America/Port_of_Spain"))).date()
    except Exception:
        return date.today()


# ---------------------------------------------------------------------------
# Form helpers
# ---------------------------------------------------------------------------
CLIENT_FIELDS = [
    ("first_name", "First name (leave blank for a business)", "text", None, False),
    ("last_name", "Last name / business name", "text", None, True),
    ("phone", "Phone", "tel", None, False),
    ("alt_phone", "Alternate phone", "tel", None, False),
    ("email", "Email", "email", None, False),
    ("preferred_contact", "Preferred contact", "select", CONTACT_PREFS, False),
    ("dob", "Date of birth", "date", None, False),
    ("address", "Street address", "text", None, False),
    ("city", "City", "text", None, False),
    ("state", "State", "text", None, False),
    ("zip", "ZIP", "text", None, False),
    ("notes", "Notes", "textarea", None, False),
]

POLICY_FIELDS = [
    ("client_id", "Client", "client", None, True),
    ("policy_type", "Policy type", "select", POLICY_TYPES, True),
    ("policy_number", "Policy number", "text", None, False),
    ("carrier", "Carrier / company", "text", None, False),
    ("status", "Status", "select", POLICY_STATUSES, True),
    ("effective_date", "Effective date", "date", None, False),
    ("expiration_date", "Expiration / renewal date", "date", None, False),
    ("premium", "Premium per payment ($)", "number", None, False),
    ("payment_frequency", "Payment frequency", "select", FREQUENCIES, False),
    ("next_due_date", "Next payment due", "date", None, False),
    ("payment_method", "Payment method", "select", PAYMENT_METHODS, False),
    ("coverage_amount", "Coverage limit ($)", "number", None, False),
    ("deductible", "Deductible ($)", "number", None, False),
    ("commission_rate", "Commission (%)", "number", None, False),
    ("insured_item", "Insured item (vehicle, property, business…)", "textarea", None, False),
    ("beneficiaries", "Beneficiaries / additional insureds", "textarea", None, False),
    ("notes", "Notes", "textarea", None, False),
]

MEETING_FIELDS = [
    ("title", "Title", "text", None, True),
    ("meeting_type", "Meeting type", "select", MEETING_TYPES, False),
    ("client_id", "Client (optional)", "client_optional", None, False),
    ("date", "Date", "date", None, True),
    ("start_time", "Start time", "time", None, False),
    ("end_time", "End time", "time", None, False),
    ("location", "Location / phone / link", "text", None, False),
    ("notes", "Notes / agenda", "textarea", None, False),
]

REMINDER_FIELDS = [
    ("title", "Reminder", "text", None, True),
    ("due_date", "Due date", "date", None, True),
    ("client_id", "Client (optional)", "client_optional", None, False),
    ("notes", "Notes", "textarea", None, False),
]


def read_form(fields):
    """Pull values for the given fields from request.form; return (values, errors)."""
    values, errors = {}, []
    for name, label, ftype, _opts, required in fields:
        raw = request.form.get(name, "").strip()
        if required and not raw:
            errors.append(f"{label} is required.")
        if ftype == "number":
            if raw:
                try:
                    values[name] = float(raw.replace(",", "").replace("$", ""))
                except ValueError:
                    errors.append(f"{label} must be a number.")
                    values[name] = None
            else:
                values[name] = None
        elif ftype in ("client", "client_optional"):
            values[name] = int(raw) if raw.isdigit() else None
        elif ftype == "date":
            if raw and not parse_date(raw):
                errors.append(f"{label} must be a valid date.")
            values[name] = raw or None
        else:
            values[name] = raw or None
    return values, errors


def insert(table, values):
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    return execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(values.values()))


def update(table, row_id, values):
    sets = ", ".join(f"{k} = ?" for k in values)
    execute(f"UPDATE {table} SET {sets} WHERE id = ?", (*values.values(), row_id))


def get_or_404(table, row_id):
    row = query(f"SELECT * FROM {table} WHERE id = ?", (row_id,), one=True)
    if row is None:
        abort(404)
    return row


def all_clients():
    return query("SELECT id, first_name, last_name FROM clients ORDER BY last_name, first_name")


# ---------------------------------------------------------------------------
# Reminder logic
# ---------------------------------------------------------------------------
POLICY_LIST_SQL = """
    SELECT p.*, c.first_name, c.last_name, c.phone, c.email
    FROM policies p JOIN clients c ON c.id = p.client_id
"""


def payment_alerts(days=DUE_SOON_DAYS):
    """Active policies with a payment overdue or due within `days`."""
    t = today()
    horizon = (t + timedelta(days=days)).isoformat()
    rows = query(POLICY_LIST_SQL + """
        WHERE p.status = 'Active' AND p.next_due_date IS NOT NULL
          AND p.next_due_date <= ?
        ORDER BY p.next_due_date""", (horizon,))
    overdue, upcoming = [], []
    for r in rows:
        d = parse_date(r["next_due_date"])
        item = dict(r)
        item["days"] = (d - t).days
        (overdue if d < t else upcoming).append(item)
    return overdue, upcoming


def renewal_alerts(days=RENEWAL_WINDOW_DAYS):
    t = today()
    rows = query(POLICY_LIST_SQL + """
        WHERE p.status IN ('Active', 'Pending') AND p.expiration_date IS NOT NULL
          AND p.expiration_date BETWEEN ? AND ?
        ORDER BY p.expiration_date""", (t.isoformat(), (t + timedelta(days=days)).isoformat()))
    out = []
    for r in rows:
        item = dict(r)
        item["days"] = (parse_date(r["expiration_date"]) - t).days
        out.append(item)
    return out


def task_reminders(include_done=False):
    sql = """SELECT r.*, c.first_name, c.last_name FROM reminders r
             LEFT JOIN clients c ON c.id = r.client_id"""
    if not include_done:
        sql += " WHERE r.done = 0"
    sql += " ORDER BY r.done, r.due_date"
    out = []
    for r in query(sql):
        item = dict(r)
        item["days"] = (parse_date(r["due_date"]) - today()).days
        out.append(item)
    return out


def _secret_key(db_path):
    """Keep a random session key next to the database so logins survive restarts."""
    path = os.path.join(os.path.dirname(os.path.abspath(db_path)), ".tracker_secret")
    try:
        with open(path) as f:
            key = f.read().strip()
        if key:
            return key
    except OSError:
        pass
    key = os.urandom(32).hex()
    try:
        with open(path, "w") as f:
            f.write(key)
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def _pw_fingerprint(pw):
    # Changing the password logs out every existing session.
    return hashlib.sha256(("broker-desk:" + pw).encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
def create_app(database=None):
    app = Flask(__name__)
    app.config["DATABASE"] = database or os.environ.get(
        "TRACKER_DB", os.path.join(os.path.dirname(os.path.dirname(__file__)), "insurance.db"))
    app.config["SECRET_KEY"] = os.environ.get("TRACKER_SECRET") or _secret_key(app.config["DATABASE"])
    app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # spreadsheet uploads
    # Password protection: required whenever TRACKER_PASSWORD is set (always set it when hosted).
    app.config["PASSWORD"] = os.environ.get("TRACKER_PASSWORD", "")
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = os.environ.get("TRACKER_HTTPS", "") == "1"
    app.permanent_session_lifetime = timedelta(days=14)
    db.init_db(app.config["DATABASE"])
    app.teardown_appcontext(db.close_db)

    @app.errorhandler(500)
    def server_error(err):
        original = getattr(err, "original_exception", None) or err
        detail = "".join(traceback.format_exception_only(type(original), original)).strip()
        return render_template("error.html", detail=detail), 500

    # ---------------- Login ----------------
    @app.before_request
    def require_login():
        if not app.config["PASSWORD"] or request.endpoint in ("login", "static"):
            return None
        if session.get("auth") != _pw_fingerprint(app.config["PASSWORD"]):
            return redirect(url_for("login", next=request.full_path))
        return None

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            if hmac.compare_digest(request.form.get("password", "").encode(), app.config["PASSWORD"].encode()):
                session.clear()
                session.permanent = True
                session["auth"] = _pw_fingerprint(app.config["PASSWORD"])
                nxt = request.args.get("next", "")
                return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else "/")
            time.sleep(1)  # slow down password guessing
            error = "Wrong password."
        return render_template("login.html", error=error)

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/backup")
    def backup():
        """Download a consistent copy of the whole database."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        src = sqlite3.connect(app.config["DATABASE"])
        dst = sqlite3.connect(tmp.name)
        src.backup(dst)
        dst.close()
        src.close()
        with open(tmp.name, "rb") as f:
            data = f.read()
        os.unlink(tmp.name)
        name = f"insurance-backup-{today().isoformat()}.db"
        return Response(data, mimetype="application/octet-stream",
                        headers={"Content-Disposition": f"attachment; filename={name}"})

    @app.template_filter("money")
    def money(v):
        if v is None or v == "":
            return ""
        try:
            n = float(v)
        except (TypeError, ValueError):
            return str(v)
        return f"-${-n:,.2f}" if n < 0 else f"${n:,.2f}"

    @app.template_filter("plainnum")
    def plainnum(v):
        if v is None or v == "":
            return ""
        try:
            return f"{float(v):.2f}".rstrip("0").rstrip(".")
        except (TypeError, ValueError):
            return str(v)

    @app.template_filter("pct")
    def pct(v):
        n = plainnum(v)
        return f"{n}%" if n else "—"

    @app.template_filter("nicedate")
    def nicedate(v):
        d = parse_date(v) if isinstance(v, str) else v
        return d.strftime("%b %d, %Y") if d else ""

    @app.template_filter("nicetime")
    def nicetime(v):
        if not v:
            return ""
        try:
            return datetime.strptime(v, "%H:%M").strftime("%I:%M %p").lstrip("0")
        except ValueError:
            return v

    @app.template_filter("relday")
    def relday(days):
        if days == 0:
            return "today"
        if days == 1:
            return "tomorrow"
        if days == -1:
            return "1 day overdue"
        if days < 0:
            return f"{-days} days overdue"
        return f"in {days} days"

    @app.context_processor
    def inject():
        overdue, _ = payment_alerts()
        due_tasks = [r for r in task_reminders() if r["days"] <= 0]
        return {"nav_alert_count": len(overdue) + len(due_tasks), "today": today()}

    # ---------------- Dashboard ----------------
    @app.route("/")
    def dashboard():
        overdue, upcoming = payment_alerts()
        t = today().isoformat()
        week = (today() + timedelta(days=7)).isoformat()
        meetings = query("""SELECT m.*, c.first_name, c.last_name FROM meetings m
                            LEFT JOIN clients c ON c.id = m.client_id
                            WHERE m.date BETWEEN ? AND ? ORDER BY m.date, m.start_time""", (t, week))
        stats = query("""SELECT
                (SELECT COUNT(*) FROM clients) AS clients,
                (SELECT COUNT(*) FROM policies WHERE status = 'Active') AS active,
                (SELECT COALESCE(SUM(CASE payment_frequency
                    WHEN 'Monthly' THEN premium * 12 WHEN 'Quarterly' THEN premium * 4
                    WHEN 'Semi-Annual' THEN premium * 2 ELSE premium END), 0)
                 FROM policies WHERE status = 'Active') AS annual_premium,
                (SELECT COALESCE(SUM(CASE payment_frequency
                    WHEN 'Monthly' THEN premium * 12 WHEN 'Quarterly' THEN premium * 4
                    WHEN 'Semi-Annual' THEN premium * 2 ELSE premium END
                    * COALESCE(commission_rate, 0) / 100), 0)
                 FROM policies WHERE status = 'Active') AS annual_commission,
                (SELECT COALESCE(SUM(amount), 0) FROM commissions
                 WHERE statement_month LIKE ?) AS commission_ytd""", (f"{today().year}-%",), one=True)
        tasks = [r for r in task_reminders() if r["days"] <= 7]
        return render_template("dashboard.html", overdue=overdue, upcoming=upcoming,
                               renewals=renewal_alerts(), meetings=meetings, stats=stats,
                               tasks=tasks)

    # ---------------- Search ----------------
    @app.route("/search")
    def search():
        q = request.args.get("q", "").strip()
        like = f"%{q}%"
        clients = policies = []
        if q:
            clients = query("""SELECT * FROM clients WHERE first_name || ' ' || last_name LIKE ?
                               OR email LIKE ? OR phone LIKE ? ORDER BY last_name""", (like, like, like))
            policies = query(POLICY_LIST_SQL + """WHERE p.policy_number LIKE ? OR p.carrier LIKE ?
                               OR p.policy_type LIKE ? OR p.insured_item LIKE ?
                               ORDER BY c.last_name""", (like, like, like, like))
        return render_template("search.html", q=q, clients=clients, policies=policies)

    # ---------------- Clients ----------------
    @app.route("/clients")
    def clients():
        rows = query("""SELECT c.*, COUNT(p.id) AS policy_count,
                        SUM(CASE WHEN p.status = 'Active' THEN 1 ELSE 0 END) AS active_count
                        FROM clients c LEFT JOIN policies p ON p.client_id = c.id
                        GROUP BY c.id ORDER BY c.last_name, c.first_name""")
        return render_template("clients.html", clients=rows)

    @app.route("/clients/new", methods=["GET", "POST"])
    def client_new():
        return _form(CLIENT_FIELDS, "clients", None, "New client",
                     lambda cid: url_for("client_detail", client_id=cid))

    @app.route("/clients/<int:client_id>")
    def client_detail(client_id):
        client = get_or_404("clients", client_id)
        policies = query("SELECT * FROM policies WHERE client_id = ? ORDER BY status, policy_type",
                         (client_id,))
        meetings = query("SELECT * FROM meetings WHERE client_id = ? ORDER BY date DESC, start_time",
                         (client_id,))
        reminders = query("SELECT * FROM reminders WHERE client_id = ? ORDER BY done, due_date",
                          (client_id,))
        return render_template("client_detail.html", client=client, policies=policies,
                               meetings=meetings, reminders=reminders)

    @app.route("/clients/<int:client_id>/edit", methods=["GET", "POST"])
    def client_edit(client_id):
        row = get_or_404("clients", client_id)
        return _form(CLIENT_FIELDS, "clients", row, "Edit client",
                     lambda cid: url_for("client_detail", client_id=cid))

    @app.route("/clients/<int:client_id>/delete", methods=["POST"])
    def client_delete(client_id):
        get_or_404("clients", client_id)
        execute("DELETE FROM clients WHERE id = ?", (client_id,))
        flash("Client and their policies were deleted.")
        return redirect(url_for("clients"))

    # ---------------- Policies ----------------
    @app.route("/policies")
    def policies():
        ptype = request.args.get("type", "")
        status = request.args.get("status", "")
        sql, args = POLICY_LIST_SQL + " WHERE 1=1", []
        if ptype:
            sql += " AND p.policy_type = ?"
            args.append(ptype)
        if status:
            sql += " AND p.status = ?"
            args.append(status)
        sql += " ORDER BY c.last_name, c.first_name, p.policy_type"
        return render_template("policies.html", policies=query(sql, args), ptype=ptype,
                               status=status, types=POLICY_TYPES, statuses=POLICY_STATUSES)

    @app.route("/policies/new", methods=["GET", "POST"])
    def policy_new():
        defaults = {"client_id": request.args.get("client_id", type=int), "status": "Active",
                    "payment_frequency": "Monthly"}
        return _form(POLICY_FIELDS, "policies", None, "New policy",
                     lambda pid: url_for("policy_detail", policy_id=pid), defaults=defaults)

    @app.route("/policies/<int:policy_id>")
    def policy_detail(policy_id):
        policy = query(POLICY_LIST_SQL + " WHERE p.id = ?", (policy_id,), one=True)
        if policy is None:
            abort(404)
        payments = query("SELECT * FROM payments WHERE policy_id = ? ORDER BY paid_on DESC",
                         (policy_id,))
        total_paid = sum(p["amount"] for p in payments)
        commissions = query("SELECT * FROM commissions WHERE policy_id = ? ORDER BY term_from DESC",
                            (policy_id,))
        days_due = None
        if policy["next_due_date"]:
            days_due = (parse_date(policy["next_due_date"]) - today()).days
        return render_template("policy_detail.html", p=policy, payments=payments,
                               total_paid=total_paid, days_due=days_due, commissions=commissions,
                               methods=PAYMENT_METHODS)

    @app.route("/policies/<int:policy_id>/edit", methods=["GET", "POST"])
    def policy_edit(policy_id):
        row = get_or_404("policies", policy_id)
        return _form(POLICY_FIELDS, "policies", row, "Edit policy",
                     lambda pid: url_for("policy_detail", policy_id=pid))

    @app.route("/policies/<int:policy_id>/delete", methods=["POST"])
    def policy_delete(policy_id):
        row = get_or_404("policies", policy_id)
        execute("DELETE FROM policies WHERE id = ?", (policy_id,))
        flash("Policy deleted.")
        return redirect(url_for("client_detail", client_id=row["client_id"]))

    @app.route("/policies/<int:policy_id>/payments", methods=["POST"])
    def payment_add(policy_id):
        policy = get_or_404("policies", policy_id)
        paid_on = request.form.get("paid_on") or today().isoformat()
        try:
            amount = float(request.form.get("amount", "").replace(",", "").replace("$", ""))
        except ValueError:
            flash("Payment amount must be a number.", "error")
            return redirect(url_for("policy_detail", policy_id=policy_id))
        insert("payments", {"policy_id": policy_id, "paid_on": paid_on, "amount": amount,
                            "method": request.form.get("method") or None,
                            "notes": request.form.get("notes") or None})
        msg = f"Recorded payment of ${amount:,.2f}."
        if request.form.get("advance") and policy["next_due_date"]:
            # Paying the renewal itself moves the policy into its new one-year term.
            due = parse_date(policy["next_due_date"])
            renew = tracker._renewal_date(policy)
            if renew and abs((due - renew).days) <= 45:
                new_end = add_months(renew, 12) - timedelta(days=1)
                update("policies", policy_id, {"effective_date": renew.isoformat(),
                                               "expiration_date": new_end.isoformat()})
                msg += f" Renewed: new term {renew.strftime('%b %d, %Y')} – {new_end.strftime('%b %d, %Y')}."
            months = FREQ_MONTHS.get(policy["payment_frequency"])
            if months:
                nxt = add_months(parse_date(policy["next_due_date"]), months)
                update("policies", policy_id, {"next_due_date": nxt.isoformat()})
                msg += f" Next payment due {nxt.strftime('%b %d, %Y')}."
            else:
                update("policies", policy_id, {"next_due_date": None})
                msg += " No further payments scheduled."
        flash(msg)
        return redirect(url_for("policy_detail", policy_id=policy_id))

    @app.route("/payments/<int:payment_id>/delete", methods=["POST"])
    def payment_delete(payment_id):
        row = get_or_404("payments", payment_id)
        execute("DELETE FROM payments WHERE id = ?", (payment_id,))
        flash("Payment removed (the next-due date was not changed).")
        return redirect(url_for("policy_detail", policy_id=row["policy_id"]))

    # ---------------- Commissions ----------------
    @app.route("/commissions")
    def commissions():
        month = request.args.get("month", "")
        months = [r["statement_month"] for r in query(
            "SELECT DISTINCT statement_month FROM commissions WHERE statement_month IS NOT NULL "
            "ORDER BY statement_month DESC")]
        sql = """SELECT cm.*, p.policy_number, p.policy_type, p.client_id FROM commissions cm
                 LEFT JOIN policies p ON p.id = cm.policy_id"""
        args = ()
        if month:
            sql += " WHERE cm.statement_month = ?"
            args = (month,)
        rows = query(sql + " ORDER BY cm.statement_month DESC, cm.term_from", args)
        by_month = query("""SELECT statement_month, COUNT(*) AS n, SUM(amount) AS total
                            FROM commissions GROUP BY statement_month
                            ORDER BY statement_month DESC""")
        return render_template("commissions.html", rows=rows, months=months, month=month,
                               by_month=by_month, total=sum(r["amount"] for r in rows))

    @app.template_filter("monthname")
    def monthname(v):
        try:
            return datetime.strptime(v, "%Y-%m").strftime("%B %Y")
        except (TypeError, ValueError):
            return v or "—"

    # ---------------- Monthly tracker ----------------
    def _tracker_args():
        t = today()
        span = max(1, min(request.args.get("months", 6, type=int), 24))
        start = request.args.get("start", "")
        try:
            y, m = (int(x) for x in start.split("-"))
            if not 1 <= m <= 12:
                raise ValueError
        except ValueError:
            first = add_months(date(t.year, t.month, 1), -3)
            y, m = first.year, first.month
        months = tracker.month_list((y, m), span)
        return months, span, request.args.get("status", ""), request.args.get("q", "")

    def _month_label(m):
        return datetime.strptime(m, "%Y-%m").strftime("%b %Y")

    @app.route("/tracker")
    def tracker_view():
        months, span, status, q = _tracker_args()
        rows, totals = tracker.build(today(), months, status, q)
        first = date(int(months[0][:4]), int(months[0][5:]), 1)
        step = max(1, span // 2)
        prev_start = add_months(first, -step).strftime("%Y-%m")
        next_start = add_months(first, step).strftime("%Y-%m")
        now_start = add_months(date(today().year, today().month, 1), -3).strftime("%Y-%m")
        return render_template("tracker.html", rows=rows, totals=totals, months=months,
                               span=span, status=status, q=q, statuses=tracker.STATUS,
                               prev_start=prev_start, next_start=next_start,
                               now_start=now_start, this_month=tracker.month_key(today()),
                               month_label=_month_label)

    @app.route("/tracker.xlsx")
    def tracker_export():
        months, _span, status, q = _tracker_args()
        rows, totals = tracker.build(today(), months, status, q)
        data = tracker.to_xlsx(rows, totals, months, _month_label)
        name = f"monthly-tracker-{months[0]}-to-{months[-1]}.xlsx"
        return Response(data, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f"attachment; filename={name}"})

    # ---------------- Reports ----------------
    SETTING_FIELDS = [("business_name", "Business / broker name"), ("contact_name", "Your name"),
                      ("address", "Address"), ("phone", "Phone"), ("email", "Email"),
                      ("licence", "Licence / registration no. (optional)"),
                      ("footer", "Footer note on reports (optional)")]

    @app.context_processor
    def inject_settings():
        return {"biz": db.get_settings()}

    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        if request.method == "POST":
            db.save_settings({k: request.form.get(k, "").strip() for k, _ in SETTING_FIELDS})
            flash("Letterhead saved.")
            return redirect(request.form.get("next") or url_for("reports_home"))
        return render_template("settings.html", fields=SETTING_FIELDS, values=db.get_settings(),
                               next=request.args.get("next", ""))

    @app.route("/reports")
    def reports_home():
        t = today()
        this_month = t.strftime("%Y-%m")
        last3 = add_months(date(t.year, t.month, 1), -2).strftime("%Y-%m")
        next_month = add_months(date(t.year, t.month, 1), 1).strftime("%Y-%m")
        policies = query("""SELECT p.id, p.policy_number, p.policy_type, c.first_name, c.last_name
                            FROM policies p JOIN clients c ON c.id = p.client_id
                            ORDER BY c.last_name, c.first_name, p.policy_type""")
        return render_template("reports.html", clients=all_clients(), policies=policies,
                               carriers=reports.carriers(), this_month=this_month, last3=last3,
                               next_month=next_month, statuses=POLICY_STATUSES,
                               client_reports=reports.CLIENT_REPORTS,
                               insurer_reports=reports.INSURER_REPORTS)

    def _build_report(kind):
        builder = reports.BUILDERS.get(kind)
        if builder is None:
            abort(404)
        a = request.args
        t = today()
        params = {"carrier": a.get("carrier", ""), "status": a.get("status", "Active"),
                  "start": a.get("start") or t.strftime("%Y-%m"),
                  "end": a.get("end") or a.get("start") or t.strftime("%Y-%m"),
                  "client_id": a.get("client_id", type=int), "policy_id": a.get("policy_id", type=int)}
        for k in ("start", "end"):
            try:
                datetime.strptime(params[k], "%Y-%m")
            except ValueError:
                params[k] = t.strftime("%Y-%m")
        if params["end"] < params["start"]:
            params["start"], params["end"] = params["end"], params["start"]
        if kind in ("client_summary",) and not params["client_id"]:
            flash("Choose a client for this report.", "error")
            return None
        if kind in ("renewal_notice", "policy_history") and not params["policy_id"]:
            flash("Choose a policy for this report.", "error")
            return None
        rep = builder(t, **params)
        if rep is None:
            abort(404)
        return rep

    @app.route("/reports/<kind>")
    def report_view(kind):
        rep = _build_report(kind)
        if rep is None:
            return redirect(url_for("reports_home"))
        return render_template("report.html", r=rep, kind=kind,
                               xlsx_url=url_for("report_xlsx", kind=kind, **request.args))

    @app.route("/reports/<kind>.xlsx")
    def report_xlsx(kind):
        rep = _build_report(kind)
        if rep is None:
            return redirect(url_for("reports_home"))
        name = f"{rep['title']} {rep.get('subtitle') or ''}".strip()
        safe = "".join(ch if ch.isalnum() else "-" for ch in name).strip("-").lower()[:80] or "report"
        return Response(reports.to_xlsx(rep),
                        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f"attachment; filename={safe}.xlsx"})

    # ---------------- Import spreadsheets ----------------
    @app.route("/import", methods=["GET", "POST"])
    def import_view():
        if request.method == "GET":
            return render_template("import.html", report=None,
                                   carrier=request.args.get("carrier", "Maritime General Insurance"))
        files = [f for f in request.files.getlist("files") if f and f.filename]
        carrier = request.form.get("carrier", "").strip()
        if not files:
            flash("Choose at least one spreadsheet to import.", "error")
            return redirect(url_for("import_view"))
        report = importer.Report()
        # Commission statements first so renewals already invoiced aren't flagged as due.
        files.sort(key=lambda f: 0 if "comm" in f.filename.lower() else 1)
        tmp = tempfile.mkdtemp(dir=os.path.dirname(os.path.abspath(app.config["DATABASE"])))
        try:
            for f in files:
                path = os.path.join(tmp, secure_filename(f.filename) or "upload.xls")
                try:
                    f.save(path)
                    with db.transaction():  # each file is saved completely or not at all
                        importer.import_file(path, carrier, display_name=f.filename, report=report)
                except Exception as exc:
                    app.logger.exception("Import of %s failed", f.filename)
                    report.add("Warning", f"{f.filename} was NOT imported. Nothing from this file "
                               f"was saved. Error: {type(exc).__name__}: {exc}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return render_template("import.html", report=report, carrier=carrier)

    # ---------------- Reminders ----------------
    @app.route("/reminders", methods=["GET", "POST"])
    def reminders():
        if request.method == "POST":
            values, errors = read_form(REMINDER_FIELDS)
            if errors:
                for e in errors:
                    flash(e, "error")
            else:
                insert("reminders", values)
                flash("Reminder added.")
            return redirect(request.form.get("next") or url_for("reminders"))
        days = request.args.get("days", DUE_SOON_DAYS, type=int)
        overdue, upcoming = payment_alerts(days)
        return render_template("reminders.html", overdue=overdue, upcoming=upcoming,
                               renewals=renewal_alerts(), tasks=task_reminders(include_done=True),
                               days=days, clients=all_clients(),
                               default_due=today().isoformat())

    @app.route("/reminders/<int:rid>/toggle", methods=["POST"])
    def reminder_toggle(rid):
        row = get_or_404("reminders", rid)
        update("reminders", rid, {"done": 0 if row["done"] else 1})
        return redirect(request.form.get("next") or url_for("reminders"))

    @app.route("/reminders/<int:rid>/delete", methods=["POST"])
    def reminder_delete(rid):
        get_or_404("reminders", rid)
        execute("DELETE FROM reminders WHERE id = ?", (rid,))
        return redirect(request.form.get("next") or url_for("reminders"))

    # ---------------- Calendar / meetings ----------------
    @app.route("/calendar")
    def calendar_view():
        t = today()
        year = request.args.get("year", t.year, type=int)
        month = request.args.get("month", t.month, type=int)
        if not 1 <= month <= 12:
            month = t.month
        cal = calendar.Calendar(firstweekday=6)  # weeks start on Sunday
        weeks = cal.monthdatescalendar(year, month)
        start, end = weeks[0][0].isoformat(), weeks[-1][-1].isoformat()

        events = {}

        def add(day, kind, label, link, time=None):
            events.setdefault(day, []).append(
                {"kind": kind, "label": label, "link": link, "time": time})

        for m in query("""SELECT m.*, c.first_name, c.last_name FROM meetings m
                          LEFT JOIN clients c ON c.id = m.client_id
                          WHERE m.date BETWEEN ? AND ? ORDER BY m.start_time""", (start, end)):
            label = m["title"] + (f" ({m['first_name']} {m['last_name']})" if m["first_name"] else "")
            add(m["date"], "meeting", label, url_for("meeting_edit", meeting_id=m["id"]),
                m["start_time"])
        for p in query(POLICY_LIST_SQL + """WHERE p.status = 'Active'
                       AND p.next_due_date BETWEEN ? AND ?""", (start, end)):
            add(p["next_due_date"], "payment",
                f"Payment due: {p['first_name']} {p['last_name']} – {p['policy_type']}",
                url_for("policy_detail", policy_id=p["id"]))
        for p in query(POLICY_LIST_SQL + """WHERE p.status IN ('Active','Pending')
                       AND p.expiration_date BETWEEN ? AND ?""", (start, end)):
            add(p["expiration_date"], "renewal",
                f"Renewal: {p['first_name']} {p['last_name']} – {p['policy_type']}",
                url_for("policy_detail", policy_id=p["id"]))
        for r in query("SELECT * FROM reminders WHERE done = 0 AND due_date BETWEEN ? AND ?",
                       (start, end)):
            add(r["due_date"], "task", r["title"], url_for("reminders"))
        for day in events.values():
            day.sort(key=lambda e: (e["kind"] != "meeting", e["time"] or ""))

        prev_m = date(year, month, 1) - timedelta(days=1)
        next_m = date(year, month, 28) + timedelta(days=4)
        return render_template("calendar.html", weeks=weeks, month=month, year=year,
                               month_name=calendar.month_name[month], events=events,
                               prev=(prev_m.year, prev_m.month), next=(next_m.year, next_m.month))

    @app.route("/meetings")
    def meetings():
        rows = query("""SELECT m.*, c.first_name, c.last_name FROM meetings m
                        LEFT JOIN clients c ON c.id = m.client_id
                        WHERE m.date >= ? ORDER BY m.date, m.start_time""", (today().isoformat(),))
        return render_template("meetings.html", meetings=rows)

    @app.route("/meetings/new", methods=["GET", "POST"])
    def meeting_new():
        defaults = {"date": request.args.get("date") or today().isoformat(),
                    "client_id": request.args.get("client_id", type=int),
                    "meeting_type": "Client Meeting"}
        return _form(MEETING_FIELDS, "meetings", None, "New meeting",
                     lambda _id: url_for("calendar_view", **_ym(request.form.get("date"))),
                     defaults=defaults)

    @app.route("/meetings/<int:meeting_id>/edit", methods=["GET", "POST"])
    def meeting_edit(meeting_id):
        row = get_or_404("meetings", meeting_id)
        return _form(MEETING_FIELDS, "meetings", row, "Edit meeting",
                     lambda _id: url_for("calendar_view", **_ym(request.form.get("date"))),
                     extra_links=[("Add to phone / Outlook (.ics)",
                                   url_for("meeting_ics", meeting_id=meeting_id))])

    @app.route("/meetings/<int:meeting_id>/delete", methods=["POST"])
    def meeting_delete(meeting_id):
        row = get_or_404("meetings", meeting_id)
        execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))
        flash("Meeting deleted.")
        return redirect(url_for("calendar_view", **_ym(row["date"])))

    @app.route("/meetings/<int:meeting_id>.ics")
    def meeting_ics(meeting_id):
        m = get_or_404("meetings", meeting_id)
        d = m["date"].replace("-", "")

        def esc(s):
            return (s or "").replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;") \
                .replace("\n", "\\n")

        if m["start_time"]:
            dtstart = f"DTSTART:{d}T{m['start_time'].replace(':', '')}00"
            end = m["end_time"] or (datetime.strptime(m["start_time"], "%H:%M")
                                    + timedelta(hours=1)).strftime("%H:%M")
            dtend = f"DTEND:{d}T{end.replace(':', '')}00"
        else:
            nxt = (parse_date(m["date"]) + timedelta(days=1)).strftime("%Y%m%d")
            dtstart, dtend = f"DTSTART;VALUE=DATE:{d}", f"DTEND;VALUE=DATE:{nxt}"
        body = "\r\n".join([
            "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Insurance Tracker//EN",
            "BEGIN:VEVENT", f"UID:meeting-{m['id']}@insurance-tracker",
            f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            dtstart, dtend, f"SUMMARY:{esc(m['title'])}",
            f"LOCATION:{esc(m['location'])}", f"DESCRIPTION:{esc(m['notes'])}",
            "BEGIN:VALARM", "TRIGGER:-PT30M", "ACTION:DISPLAY", "DESCRIPTION:Reminder",
            "END:VALARM", "END:VEVENT", "END:VCALENDAR", ""])
        return Response(body, mimetype="text/calendar",
                        headers={"Content-Disposition": f"attachment; filename=meeting-{m['id']}.ics"})

    # ---------------- Generic create/edit ----------------
    def _ym(datestr):
        d = parse_date(datestr) or today()
        return {"year": d.year, "month": d.month}

    def _form(fields, table, row, title, success_url, defaults=None, extra_links=None):
        values = dict(row) if row else dict(defaults or {})
        errors = []
        if request.method == "POST":
            values, errors = read_form(fields)
            if table == "clients":
                values["first_name"] = values.get("first_name") or ""
            if not errors:
                if row:
                    update(table, row["id"], values)
                    row_id = row["id"]
                    flash("Saved.")
                else:
                    row_id = insert(table, values)
                    flash("Created.")
                return redirect(success_url(row_id))
        delete_url = None
        if row:
            delete_url = url_for({"clients": "client_delete", "policies": "policy_delete",
                                  "meetings": "meeting_delete"}[table],
                                 **{{"clients": "client_id", "policies": "policy_id",
                                     "meetings": "meeting_id"}[table]: row["id"]})
        return render_template("form.html", fields=fields, values=values, errors=errors,
                               title=title, clients=all_clients(), delete_url=delete_url,
                               extra_links=extra_links or [])

    return app

