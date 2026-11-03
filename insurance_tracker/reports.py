"""Printable reports for clients and insurance companies.

Every report is a plain dict so one template can print it and one function can export it
to Excel:

    {"title", "subtitle", "audience": "client" | "insurer" | "internal",
     "addressee": {...} | None, "subject", "paragraphs": [...], "closing": [...],
     "sections": [{"heading", "note", "kind": "table" | "details",
                   "columns": [(key, label, fmt)], "rows": [dict], "totals": dict | None,
                   "items": [(label, value)]}]}

fmt is one of: text, money, date, pct, int.
"""
from datetime import date, timedelta

from . import tracker
from .db import query

CLIENT_REPORTS = {
    "client_summary": "Client policy summary",
    "renewal_notice": "Renewal notice",
}
INSURER_REPORTS = {
    "commission_reconciliation": "Commission reconciliation / query",
    "renewals": "Renewals for a period",
    "outstanding": "Outstanding premiums",
    "book": "Book of business (policies in force)",
    "policy_history": "Policy history",
}
ALL_REPORTS = {**CLIENT_REPORTS, **INSURER_REPORTS}


def _d(s):
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


def _name(row):
    return f"{row['first_name']} {row['last_name']}".strip()


def _period(p):
    a, b = _d(p["effective_date"]), _d(p["expiration_date"])
    if a and b:
        return f"{a:%d %b %Y} – {b:%d %b %Y}"
    if b:
        return f"to {b:%d %b %Y}"
    return ""


def _month_label(m):
    y, mo = m.split("-")
    return date(int(y), int(mo), 1).strftime("%B %Y")


def _months_between(start, end):
    y, m = (int(x) for x in start.split("-"))
    ey, em = (int(x) for x in end.split("-"))
    count = (ey - y) * 12 + (em - m) + 1
    return tracker.month_list((y, m), max(1, min(count, 36)))


def _addressee(client):
    lines = [client["address"], client["city"], client["state"], client["zip"]]
    return {"name": _name(client), "lines": [x for x in lines if x],
            "phone": client["phone"], "email": client["email"]}


def _carrier_ok(p, carrier):
    return not carrier or (p["carrier"] or "").strip().lower() == carrier.strip().lower()


def carriers():
    return [r["carrier"] for r in query(
        "SELECT DISTINCT carrier FROM policies WHERE carrier IS NOT NULL AND carrier != '' ORDER BY carrier")]


def _total(rows, *keys):
    return {k: round(sum((r.get(k) or 0) for r in rows), 2) for k in keys}


# ---------------------------------------------------------------------------
# Client-facing
# ---------------------------------------------------------------------------
def client_summary(today, client_id, **_):
    c = query("SELECT * FROM clients WHERE id = ?", (client_id,), one=True)
    if c is None:
        return None
    policies = query("""SELECT * FROM policies WHERE client_id = ?
                        ORDER BY CASE status WHEN 'Active' THEN 0 ELSE 1 END, policy_type""", (client_id,))
    prows = [{"policy_number": p["policy_number"], "policy_type": p["policy_type"],
              "carrier": p["carrier"], "period": _period(p), "item": p["insured_item"],
              "coverage": p["coverage_amount"], "premium": p["premium"],
              "frequency": p["payment_frequency"], "status": p["status"]} for p in policies]
    due = [{"date": p["next_due_date"], "policy_number": p["policy_number"],
            "policy_type": p["policy_type"], "amount": p["premium"] if p["premium"] else "To be advised"}
           for p in policies if p["status"] == "Active" and p["next_due_date"]]
    due.sort(key=lambda r: r["date"])
    since = (today - timedelta(days=730)).isoformat()
    pays = query("""SELECT pay.*, p.policy_number, p.policy_type FROM payments pay
                    JOIN policies p ON p.id = pay.policy_id
                    WHERE p.client_id = ? AND pay.paid_on >= ? ORDER BY pay.paid_on DESC""",
                 (client_id, since))
    payrows = [{"date": x["paid_on"], "policy_number": x["policy_number"],
                "policy_type": x["policy_type"], "amount": x["amount"], "method": x["method"]}
               for x in pays]
    sections = [
        {"heading": "Your policies", "kind": "table", "rows": prows,
         "columns": [("policy_number", "Policy #", "text"), ("policy_type", "Cover", "text"),
                     ("carrier", "Insurer", "text"), ("period", "Period of insurance", "text"),
                     ("item", "Insured item / property", "text"), ("coverage", "Sum insured", "money"),
                     ("premium", "Premium", "money"), ("status", "Status", "text")]},
    ]
    if due:
        sections.append({"heading": "Payments and renewals coming up", "kind": "table", "rows": due,
                         "columns": [("date", "Due date", "date"), ("policy_number", "Policy #", "text"),
                                     ("policy_type", "Cover", "text"), ("amount", "Amount", "money")],
                         "totals": _total([d for d in due if isinstance(d["amount"], float)], "amount")
                         if any(isinstance(d["amount"], float) for d in due) else None})
    sections.append({"heading": "Payments received (last 2 years)", "kind": "table", "rows": payrows,
                     "columns": [("date", "Date", "date"), ("policy_number", "Policy #", "text"),
                                 ("policy_type", "Cover", "text"), ("amount", "Amount", "money"),
                                 ("method", "Method", "text")],
                     "totals": _total(payrows, "amount") if payrows else None,
                     "empty": "No payments recorded in the last two years."})
    return {
        "title": "Policy summary", "audience": "client", "addressee": _addressee(c),
        "subject": f"Summary of your insurance policies as at {today:%d %B %Y}",
        "paragraphs": [f"Dear {c['first_name'] or _name(c)},",
                       "Please find below a summary of the insurance policies arranged through our "
                       "office. Kindly check the details and let us know if anything has changed, "
                       "such as your address, vehicle or property, so your cover stays correct."],
        "sections": sections,
        "closing": ["If you have any questions about your cover or payments, please contact us."],
    }


def renewal_notice(today, policy_id, **_):
    p = query("""SELECT p.*, c.first_name, c.last_name, c.address, c.city, c.state, c.zip,
                 c.phone, c.email FROM policies p JOIN clients c ON c.id = p.client_id
                 WHERE p.id = ?""", (policy_id,), one=True)
    if p is None:
        return None
    renew = _d(p["next_due_date"]) or tracker._renewal_date(p)
    when = f"{renew:%d %B %Y}" if renew else "the renewal date"
    money = f"${p['premium']:,.2f}" if p["premium"] else "the renewal premium"
    items = [("Policy number", p["policy_number"]), ("Cover", p["policy_type"]),
             ("Insurer", p["carrier"]), ("Insured item / property", p["insured_item"]),
             ("Sum insured", f"${p['coverage_amount']:,.2f}" if p["coverage_amount"] else None),
             ("Current period", _period(p)), ("Renewal date", f"{renew:%d %B %Y}" if renew else None),
             ("Premium payable", f"${p['premium']:,.2f}" if p["premium"] else None),
             ("Payment method on file", p["payment_method"])]
    return {
        "title": "Renewal notice", "audience": "client", "addressee": _addressee(p),
        "subject": f"Renewal of your {p['policy_type']} policy"
                   + (f" #{p['policy_number']}" if p["policy_number"] else ""),
        "paragraphs": [f"Dear {p['first_name'] or _name(p)},",
                       f"Your {p['policy_type']} policy"
                       + (f" with {p['carrier']}" if p["carrier"] else "")
                       + f" is due for renewal on {when}. The premium payable is {money}.",
                       "To keep your cover in force without interruption, please arrange payment "
                       "on or before the renewal date. If your circumstances have changed (a new "
                       "vehicle, renovations, a change of address or use), please let us know before "
                       "renewal so we can update your policy."],
        "sections": [{"heading": "Policy details", "kind": "details",
                      "items": [(k, v) for k, v in items if v]}],
        "closing": ["Thank you for your continued business. Please contact us with any questions."],
    }


# ---------------------------------------------------------------------------
# For insurance companies / internal
# ---------------------------------------------------------------------------
def commission_reconciliation(today, start, end, carrier="", **_):
    months = _months_between(start, end)
    rows = []
    for c in query("""SELECT cm.*, p.policy_number, p.policy_type, p.carrier, p.client_id
                      FROM commissions cm LEFT JOIN policies p ON p.id = cm.policy_id
                      WHERE cm.statement_month BETWEEN ? AND ?
                      ORDER BY cm.statement_month, cm.term_from""", (months[0], months[-1])):
        if carrier and (c["carrier"] or "").lower() != carrier.lower():
            continue
        flag = ""
        if c["premium"] is not None and c["premium"] <= 0:
            flag = "Adjustment / negative premium"
        rows.append({"statement": _month_label(c["statement_month"]) if c["statement_month"] else "",
                     "invoice": c["invoice_no"], "client": c["client_name"],
                     "policy_number": c["policy_number"] or "", "code": c["class_code"],
                     "term": f"{_d(c['term_from']):%d %b %Y} – {_d(c['term_to']):%d %b %Y}"
                     if c["term_from"] and c["term_to"] else "",
                     "premium": c["premium"], "rate": c["rate"], "amount": c["amount"], "flag": flag})
    trows, _totals = tracker.build(today, months)
    awaiting, unpaid = [], []
    for r in trows:
        p = r["policy"]
        if not _carrier_ok(p, carrier):
            continue
        for m in months:
            for cell in r["cells"].get(m, []):
                line = {"date": cell["date"].isoformat(), "client": r["name"],
                        "policy_number": p["policy_number"], "policy_type": p["policy_type"],
                        "premium": cell["premium"], "paid": cell["paid"] or None,
                        "rate": p["commission_rate"], "expected": cell["expected"]}
                if cell["status"] == "awaiting":
                    awaiting.append(line)
                elif cell["status"] == "unpaid":
                    unpaid.append(line)
    flagged = [r for r in rows if r["flag"]]
    sections = [
        {"heading": "Commission received", "kind": "table", "rows": rows,
         "note": f"All commission lines on statements from {_month_label(months[0])} to "
                 f"{_month_label(months[-1])}.",
         "columns": [("statement", "Statement", "text"), ("invoice", "Invoice", "text"),
                     ("client", "Client", "text"), ("policy_number", "Policy #", "text"),
                     ("code", "Class", "text"), ("term", "Term", "text"),
                     ("premium", "Premium", "money"), ("rate", "Rate", "pct"),
                     ("amount", "Commission", "money")],
         "totals": _total(rows, "premium", "amount")},
        {"heading": "Query: renewals paid but commission not yet received", "kind": "table",
         "rows": awaiting,
         "note": "Renewals in this period where the client paid (payment recorded) but no commission "
                 "has appeared on a statement yet.",
         "columns": [("date", "Renewal date", "date"), ("client", "Client", "text"),
                     ("policy_number", "Policy #", "text"), ("policy_type", "Cover", "text"),
                     ("premium", "Premium", "money"), ("paid", "Paid", "money"),
                     ("rate", "Rate", "pct"), ("expected", "Commission expected", "money")],
         "totals": _total(awaiting, "premium", "paid", "expected"),
         "empty": "None. Every paid renewal in this period has commission recorded."},
    ]
    if flagged:
        sections.append({"heading": "Query: adjustment lines to confirm", "kind": "table", "rows": flagged,
                         "note": "Statement lines with a zero or negative premium.",
                         "columns": [("statement", "Statement", "text"), ("invoice", "Invoice", "text"),
                                     ("client", "Client", "text"), ("policy_number", "Policy #", "text"),
                                     ("term", "Term", "text"), ("premium", "Premium", "money"),
                                     ("amount", "Commission", "money")]})
    sections.append({"heading": "Renewals in this period not yet paid", "kind": "table", "rows": unpaid,
                     "columns": [("date", "Renewal date", "date"), ("client", "Client", "text"),
                                 ("policy_number", "Policy #", "text"),
                                 ("policy_type", "Cover", "text"), ("premium", "Premium", "money"),
                                 ("expected", "Commission if paid", "money")],
                     "totals": _total(unpaid, "premium", "expected"),
                     "empty": "None."})
    return {"title": "Commission reconciliation", "audience": "insurer",
            "subtitle": f"{_month_label(months[0])} – {_month_label(months[-1])}"
                        + (f" · {carrier}" if carrier else ""),
            "sections": sections}


def renewals(today, start, end, carrier="", **_):
    months = _months_between(start, end)
    trows, _ = tracker.build(today, months)
    rows = []
    for r in trows:
        p = r["policy"]
        if not _carrier_ok(p, carrier):
            continue
        for m in months:
            for cell in r["cells"].get(m, []):
                if cell["status"] == "payment":
                    continue
                rows.append({"date": cell["date"].isoformat(), "client": r["name"],
                             "phone": p["phone"], "policy_number": p["policy_number"],
                             "policy_type": p["policy_type"], "carrier": p["carrier"],
                             "premium": cell["premium"], "status": cell["label"],
                             "paid": cell["paid"] or None, "commission": cell["commission"] or None})
    rows.sort(key=lambda x: x["date"])
    return {"title": "Renewals", "audience": "insurer",
            "subtitle": f"{_month_label(months[0])} – {_month_label(months[-1])}"
                        + (f" · {carrier}" if carrier else ""),
            "sections": [{"heading": f"{len(rows)} renewals", "kind": "table", "rows": rows,
                          "columns": [("date", "Renewal date", "date"), ("client", "Client", "text"),
                                      ("phone", "Phone", "text"), ("policy_number", "Policy #", "text"),
                                      ("policy_type", "Cover", "text"), ("carrier", "Insurer", "text"),
                                      ("premium", "Premium", "money"), ("status", "Status", "text"),
                                      ("paid", "Paid", "money"), ("commission", "Commission", "money")],
                          "totals": _total(rows, "premium", "paid", "commission")}]}


def outstanding(today, carrier="", **_):
    start = (today.replace(day=1) - timedelta(days=365)).strftime("%Y-%m")
    months = _months_between(start, today.strftime("%Y-%m"))
    trows, _ = tracker.build(today, months)
    rows = []
    for r in trows:
        p = r["policy"]
        if not _carrier_ok(p, carrier):
            continue
        for m in months:
            for cell in r["cells"].get(m, []):
                if cell["status"] == "unpaid":
                    rows.append({"date": cell["date"].isoformat(), "days": (today - cell["date"]).days,
                                 "client": r["name"], "phone": p["phone"],
                                 "policy_number": p["policy_number"], "policy_type": p["policy_type"],
                                 "carrier": p["carrier"], "premium": cell["premium"]})
    rows.sort(key=lambda x: x["date"])
    notes = []
    for rem in query("""SELECT r.*, c.first_name, c.last_name, c.phone, p.policy_number, p.carrier
                        FROM reminders r LEFT JOIN clients c ON c.id = r.client_id
                        LEFT JOIN policies p ON p.id = r.policy_id
                        WHERE r.done = 0 AND r.title LIKE 'Collect outstanding premium%'
                        ORDER BY r.due_date"""):
        if carrier and (rem["carrier"] or "").lower() != carrier.lower():
            continue
        notes.append({"client": f"{rem['first_name'] or ''} {rem['last_name'] or ''}".strip(),
                      "phone": rem["phone"], "policy_number": rem["policy_number"],
                      "note": rem["notes"] or rem["title"]})
    sections = [{"heading": "Renewals past due with no payment recorded", "kind": "table", "rows": rows,
                 "columns": [("date", "Renewal date", "date"), ("days", "Days overdue", "int"),
                             ("client", "Client", "text"), ("phone", "Phone", "text"),
                             ("policy_number", "Policy #", "text"), ("policy_type", "Cover", "text"),
                             ("carrier", "Insurer", "text"), ("premium", "Premium", "money")],
                 "totals": _total(rows, "premium"), "empty": "No unpaid renewals in the last 12 months."}]
    if notes:
        sections.append({"heading": "Outstanding premiums reported by the insurer", "kind": "table",
                         "rows": notes,
                         "columns": [("client", "Client", "text"), ("phone", "Phone", "text"),
                                     ("policy_number", "Policy #", "text"), ("note", "Note", "text")]})
    return {"title": "Outstanding premiums", "audience": "insurer",
            "subtitle": f"As at {today:%d %B %Y}" + (f" · {carrier}" if carrier else ""),
            "sections": sections}


def book(today, carrier="", status="Active", **_):
    sql = """SELECT p.*, c.first_name, c.last_name FROM policies p JOIN clients c ON c.id = p.client_id"""
    args = []
    if status:
        sql += " WHERE p.status = ?"
        args.append(status)
    rows = []
    for p in query(sql + " ORDER BY p.carrier, c.last_name, c.first_name", args):
        if not _carrier_ok(p, carrier):
            continue
        mult = {"Monthly": 12, "Quarterly": 4, "Semi-Annual": 2}.get(p["payment_frequency"], 1)
        annual = (p["premium"] or 0) * mult if p["premium"] else None
        est = round(annual * p["commission_rate"] / 100, 2) if annual and p["commission_rate"] else None
        rows.append({"client": _name(p), "policy_number": p["policy_number"],
                     "policy_type": p["policy_type"], "carrier": p["carrier"], "period": _period(p),
                     "coverage": p["coverage_amount"], "premium": annual, "rate": p["commission_rate"],
                     "commission": est})
    return {"title": "Book of business", "audience": "insurer",
            "subtitle": (f"{status} policies" if status else "All policies")
                        + (f" · {carrier}" if carrier else "") + f" · as at {today:%d %B %Y}",
            "sections": [{"heading": f"{len(rows)} policies", "kind": "table", "rows": rows,
                          "columns": [("client", "Client", "text"), ("policy_number", "Policy #", "text"),
                                      ("policy_type", "Cover", "text"), ("carrier", "Insurer", "text"),
                                      ("period", "Period", "text"), ("coverage", "Sum insured", "money"),
                                      ("premium", "Annual premium", "money"), ("rate", "Comm. %", "pct"),
                                      ("commission", "Est. commission", "money")],
                          "totals": _total(rows, "coverage", "premium", "commission")}]}


def policy_history(today, policy_id, **_):
    p = query("""SELECT p.*, c.first_name, c.last_name, c.address, c.city, c.phone, c.email
                 FROM policies p JOIN clients c ON c.id = p.client_id WHERE p.id = ?""",
              (policy_id,), one=True)
    if p is None:
        return None
    pays = [{"date": x["paid_on"], "amount": x["amount"], "method": x["method"], "notes": x["notes"]}
            for x in query("SELECT * FROM payments WHERE policy_id = ? ORDER BY paid_on", (policy_id,))]
    comms = [{"statement": _month_label(x["statement_month"]) if x["statement_month"] else "",
              "invoice": x["invoice_no"], "code": x["class_code"],
              "term": f"{_d(x['term_from']):%d %b %Y} – {_d(x['term_to']):%d %b %Y}"
              if x["term_from"] and x["term_to"] else "",
              "premium": x["premium"], "rate": x["rate"], "amount": x["amount"]}
             for x in query("SELECT * FROM commissions WHERE policy_id = ? ORDER BY term_from", (policy_id,))]
    items = [("Insured", _name(p)), ("Address", ", ".join(x for x in (p["address"], p["city"]) if x)),
             ("Phone", p["phone"]), ("Policy number", p["policy_number"]), ("Cover", p["policy_type"]),
             ("Insurer", p["carrier"]), ("Status", p["status"]), ("Period", _period(p)),
             ("Sum insured", f"${p['coverage_amount']:,.2f}" if p["coverage_amount"] else None),
             ("Premium", f"${p['premium']:,.2f} {p['payment_frequency'] or ''}" if p["premium"] else None),
             ("Deductible", f"${p['deductible']:,.2f}" if p["deductible"] else None),
             ("Commission rate", f"{p['commission_rate']:g}%" if p["commission_rate"] is not None else None),
             ("Insured item / property", p["insured_item"]),
             ("Beneficiaries / additional insureds", p["beneficiaries"])]
    sections = [{"heading": "Policy details", "kind": "details", "items": [(k, v) for k, v in items if v]},
                {"heading": "Payments received", "kind": "table", "rows": pays,
                 "columns": [("date", "Date", "date"), ("amount", "Amount", "money"),
                             ("method", "Method", "text"), ("notes", "Notes", "text")],
                 "totals": _total(pays, "amount") if pays else None, "empty": "No payments recorded."},
                {"heading": "Commission history", "kind": "table", "rows": comms,
                 "columns": [("statement", "Statement", "text"), ("invoice", "Invoice", "text"),
                             ("code", "Class", "text"), ("term", "Term", "text"),
                             ("premium", "Premium", "money"), ("rate", "Rate", "pct"),
                             ("amount", "Commission", "money")],
                 "totals": _total(comms, "amount") if comms else None, "empty": "No commission recorded."}]
    if p["notes"]:
        sections.append({"heading": "Notes", "kind": "details", "items": [("", p["notes"])]})
    return {"title": "Policy history", "audience": "insurer",
            "subtitle": f"{p['policy_type']} #{p['policy_number'] or '—'} · {_name(p)}",
            "sections": sections}


BUILDERS = {"client_summary": client_summary, "renewal_notice": renewal_notice,
            "commission_reconciliation": commission_reconciliation, "renewals": renewals,
            "outstanding": outstanding, "book": book, "policy_history": policy_history}


def to_xlsx(report):
    import io

    import openpyxl
    from openpyxl.styles import Font

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    used = set()
    for sec in report["sections"]:
        name = "".join(ch for ch in (sec["heading"] or "Report") if ch not in "[]:*?/\\")[:28] or "Report"
        base, n = name, 2
        while name in used:
            name = f"{base[:25]} {n}"
            n += 1
        used.add(name)
        ws = wb.create_sheet(name)
        if sec["kind"] == "details":
            for k, v in sec["items"]:
                ws.append([k, v])
            ws.column_dimensions["A"].width = 30
            ws.column_dimensions["B"].width = 70
            continue
        cols = sec["columns"]
        ws.append([label for _k, label, _f in cols])
        for c in ws[1]:
            c.font = Font(bold=True)
        for r in sec["rows"]:
            line = []
            for k, _label, fmt in cols:
                v = r.get(k)
                if fmt == "date" and v:
                    v = _d(v) or v
                line.append(v)
            ws.append(line)
            for i, (_k, _l, fmt) in enumerate(cols, 1):
                cell = ws.cell(row=ws.max_row, column=i)
                if fmt == "money":
                    cell.number_format = '"$"#,##0.00'
                elif fmt == "date":
                    cell.number_format = "dd mmm yyyy"
        if sec.get("totals"):
            ws.append([("Total" if i == 0 else sec["totals"].get(k)) for i, (k, _l, _f) in enumerate(cols)])
            for c in ws[ws.max_row]:
                c.font = Font(bold=True)
                c.number_format = '"$"#,##0.00'
        for i, (_k, label, fmt) in enumerate(cols, 1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = \
                {"money": 15, "date": 14, "pct": 9, "int": 10}.get(fmt, max(14, min(40, len(label) + 14)))
        ws.freeze_panes = "A2"
    if not wb.sheetnames:
        wb.create_sheet("Report")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
