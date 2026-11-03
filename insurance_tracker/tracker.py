"""Monthly tracker: who renews when, who paid, and whether the commission came in.

Every policy gets "renewal events":
  * each commission line starts one (its term start date), with that commission attached;
  * the policy's next renewal date (from its expiration date) adds one if nothing covers it yet.
Recorded payments are attached to the nearest renewal event. A commission on a renewal also
counts as proof it was paid, because the insurer only pays commission on collected premium.
"""
from datetime import date, timedelta

from .db import query

MATCH_DAYS = 31            # a commission/renewal within this many days is the same renewal
PAY_BEFORE, PAY_AFTER = 60, 90   # a payment this close to a renewal belongs to it

STATUS = {
    "done": ("Paid · commission received", "st-done"),
    "awaiting": ("Paid · commission not yet received", "st-await"),
    "unpaid": ("Not paid", "st-unpaid"),
    "due": ("Renewal coming up", "st-due"),
    "later": ("Renewal scheduled", "st-later"),
    "inactive": ("Policy lapsed / cancelled", "st-inactive"),
    "payment": ("Payment recorded", "st-done"),
}


def _d(s):
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


def month_key(d):
    return f"{d.year}-{d.month:02d}"


def month_list(start, count):
    y, m = start
    out = []
    for _ in range(count):
        out.append(f"{y}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def _renewal_date(p):
    exp = _d(p["expiration_date"])
    if not exp:
        return None
    eff = _d(p["effective_date"])
    # Commission-based policies store the last day of the term; renewal is the day after.
    if eff and (exp - eff).days > 27:
        return exp + timedelta(days=1)
    return exp


def build(today, months, status_filter="", q=""):
    first, last = months[0], months[-1]
    policies = query("""SELECT p.*, c.first_name, c.last_name, c.phone, c.email
                        FROM policies p JOIN clients c ON c.id = p.client_id
                        ORDER BY c.last_name, c.first_name, p.policy_type""")
    comms = {}
    for c in query("SELECT * FROM commissions WHERE policy_id IS NOT NULL ORDER BY term_from"):
        comms.setdefault(c["policy_id"], []).append(c)
    pays = {}
    for pay in query("SELECT * FROM payments ORDER BY paid_on"):
        pays.setdefault(pay["policy_id"], []).append(pay)

    q = (q or "").strip().lower()
    rows = []
    totals = {m: {"renewals": 0, "paid": 0, "unpaid": 0, "awaiting": 0, "due": 0,
                  "comm_on_renewals": 0.0, "comm_expected": 0.0, "premium_due": 0.0,
                  "statement": 0.0} for m in months}

    for p in policies:
        name = f"{p['first_name']} {p['last_name']}".strip()
        if q and q not in name.lower() and q not in (p["policy_number"] or "").lower():
            continue
        events = []
        for c in comms.get(p["id"], []):
            start = _d(c["term_from"]) or _d(c["invoice_date"])
            if not start:
                continue
            ev = next((e for e in events if abs((e["date"] - start).days) <= MATCH_DAYS), None)
            if ev is None:
                ev = {"date": start, "commissions": [], "payments": [], "premium": None}
                events.append(ev)
            ev["commissions"].append(c)
            if c["premium"] is not None:
                if c["premium"] > 0:
                    ev["premium"] = c["premium"]
            elif ev["premium"] is None and c["rate"]:
                ev["premium"] = round(c["amount"] / c["rate"] * 100, 2)
        # The current term's start is a renewal too (e.g. after you record a renewal payment).
        cur = _d(p["effective_date"])
        if cur and not any(abs((e["date"] - cur).days) <= MATCH_DAYS for e in events):
            events.append({"date": cur, "commissions": [], "payments": [], "premium": p["premium"]})
        nxt = _renewal_date(p)
        if nxt and not any(abs((e["date"] - nxt).days) <= MATCH_DAYS for e in events):
            events.append({"date": nxt, "commissions": [], "payments": [], "premium": p["premium"],
                           "scheduled": True})
        events.sort(key=lambda e: e["date"])

        for pay in pays.get(p["id"], []):
            pd = _d(pay["paid_on"])
            best = None
            for e in events:
                gap = (pd - e["date"]).days
                if -PAY_BEFORE <= gap <= PAY_AFTER and (best is None or abs(gap) < abs((pd - best["date"]).days)):
                    best = e
            if best:
                best["payments"].append(pay)
            else:
                events.append({"date": pd, "commissions": [], "payments": [pay], "premium": None,
                               "payment_only": True})

        inactive = p["status"] in ("Lapsed", "Cancelled", "Expired")
        cells = {}
        row_statuses = set()
        for e in events:
            m = month_key(e["date"])
            if not (first <= m <= last):
                continue
            comm_total = sum(c["amount"] for c in e["commissions"])
            paid_total = sum(x["amount"] for x in e["payments"])
            if e.get("payment_only"):
                status = "payment"
            elif e["commissions"]:
                status = "done"
            elif e["payments"]:
                status = "awaiting"
            elif inactive:
                status = "inactive"
            elif e["date"] < today:
                status = "unpaid"
            elif (e["date"] - today).days <= 30:
                status = "due"
            else:
                status = "later"
            expected = None
            if status in ("awaiting", "unpaid", "due", "later") and e["premium"] and p["commission_rate"]:
                expected = round(e["premium"] * p["commission_rate"] / 100, 2)
            cell = {
                "date": e["date"], "status": status, "label": STATUS[status][0],
                "css": STATUS[status][1], "premium": e["premium"], "paid": paid_total,
                "payments": e["payments"], "commission": comm_total,
                "statements": sorted({c["statement_month"] for c in e["commissions"] if c["statement_month"]}),
                "expected": expected,
            }
            cells.setdefault(m, []).append(cell)
            row_statuses.add(status)
            t = totals[m]
            if status != "payment":
                t["renewals"] += 1
            if status in ("done", "awaiting"):
                t["paid"] += 1
            if status == "unpaid":
                t["unpaid"] += 1
                t["premium_due"] += e["premium"] or 0
            if status == "due":
                t["due"] += 1
                t["premium_due"] += e["premium"] or 0
            if status == "awaiting":
                t["awaiting"] += 1
            t["comm_on_renewals"] += comm_total
            t["comm_expected"] += expected or 0
        if not cells:
            continue
        if status_filter and status_filter not in row_statuses:
            continue
        rows.append({"policy": p, "name": name, "cells": cells})

    for s in query("""SELECT statement_month, SUM(amount) AS total FROM commissions
                      WHERE statement_month BETWEEN ? AND ? GROUP BY statement_month""", (first, last)):
        totals[s["statement_month"]]["statement"] = s["total"] or 0.0
    return rows, totals


def to_xlsx(rows, totals, months, month_label):
    """Return the tracker as an Excel workbook (bytes)."""
    import io

    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill

    fills = {"st-done": "C8E6C9", "st-await": "BBDEFB", "st-unpaid": "FFCDD2", "st-due": "FFE0B2",
             "st-later": "ECEFF1", "st-inactive": "E0E0E0"}
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Monthly tracker"
    head = ["Client", "Policy #", "Type", "Phone"] + [month_label(m) for m in months]
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in rows:
        p = r["policy"]
        line = [r["name"], p["policy_number"] or "", p["policy_type"], p["phone"] or ""]
        for m in months:
            parts = []
            for cell in r["cells"].get(m, []):
                txt = f"{cell['date']:%d %b}: {cell['label']}"
                if cell["premium"]:
                    txt += f" | Premium ${cell['premium']:,.2f}"
                if cell["paid"]:
                    txt += f" | Paid ${cell['paid']:,.2f}"
                if cell["commission"]:
                    txt += f" | Commission ${cell['commission']:,.2f}"
                    if cell["statements"]:
                        txt += " (" + ", ".join(month_label(s) for s in cell["statements"]) + " stmt)"
                elif cell["expected"]:
                    txt += f" | Commission expected ${cell['expected']:,.2f}"
                parts.append(txt)
            line.append("\n".join(parts))
        ws.append(line)
        for i, m in enumerate(months):
            cells = r["cells"].get(m)
            if cells:
                xl = ws.cell(row=ws.max_row, column=5 + i)
                xl.fill = PatternFill("solid", fgColor=fills.get(cells[0]["css"], "FFFFFF"))
                xl.alignment = Alignment(wrap_text=True, vertical="top")
    for col, width in zip("ABCD", (28, 16, 30, 12)):
        ws.column_dimensions[col].width = width
    for i in range(len(months)):
        ws.column_dimensions[openpyxl.utils.get_column_letter(5 + i)].width = 34
    ws.freeze_panes = "E2"

    s = wb.create_sheet("Monthly totals")
    s.append(["Month", "Renewals", "Paid", "Not paid (past due)", "Coming up (30 days)",
              "Paid, commission not received", "Premium still to collect",
              "Commission received on these renewals", "Commission still expected",
              "Commission on that month's statement"])
    for c in s[1]:
        c.font = Font(bold=True)
    for m in months:
        t = totals[m]
        s.append([month_label(m), t["renewals"], t["paid"], t["unpaid"], t["due"], t["awaiting"],
                  round(t["premium_due"], 2), round(t["comm_on_renewals"], 2),
                  round(t["comm_expected"], 2), round(t["statement"], 2)])
    for col in "ABCDEFGHIJ":
        s.column_dimensions[col].width = 20
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
