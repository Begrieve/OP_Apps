"""Insurance Broker Tracker: clients, policies, payments, reminders and a meeting calendar."""
import calendar
import os
from datetime import date, datetime, timedelta, timezone

from flask import (Flask, Response, abort, flash, redirect, render_template,
                   request, url_for)

from . import db
from .db import execute, query

# ---------------------------------------------------------------------------
# Choice lists
# ---------------------------------------------------------------------------
POLICY_TYPES = [
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
    override = os.environ.get("TRACKER_TODAY")  # used by tests
    return parse_date(override) if override else date.today()


# ---------------------------------------------------------------------------
# Form helpers
# ---------------------------------------------------------------------------
CLIENT_FIELDS = [
    ("first_name", "First name", "text", None, True),
    ("last_name", "Last name", "text", None, True),
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


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
def create_app(database=None):
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("TRACKER_SECRET", "change-me-local-only")
    app.config["DATABASE"] = database or os.environ.get(
        "TRACKER_DB", os.path.join(os.path.dirname(os.path.dirname(__file__)), "insurance.db"))
    db.init_db(app.config["DATABASE"])
    app.teardown_appcontext(db.close_db)

    @app.template_filter("money")
    def money(v):
        return "" if v is None else f"${v:,.2f}"

    @app.template_filter("plainnum")
    def plainnum(v):
        return f"{v:.2f}".rstrip("0").rstrip(".")

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
                 FROM policies WHERE status = 'Active') AS annual_commission""", one=True)
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
        days_due = None
        if policy["next_due_date"]:
            days_due = (parse_date(policy["next_due_date"]) - today()).days
        return render_template("policy_detail.html", p=policy, payments=payments,
                               total_paid=total_paid, days_due=days_due,
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

