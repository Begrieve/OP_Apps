"""Import insurer spreadsheets (renewal listings and commission statements) into the database.

Two report layouts are recognised from their header row:

* Renewal listing  - columns LOB, POLICY, RENEWAL DATE, CLIENT NAME, ADDRESS 1-3, PHONE,
                     SUM INSURED, GROSS/NET PREM, DISCOUNTS, TYPE OF COVER, RISK, REMARKS ...
* Commission statement - columns ProdCode, InvPrefix, PolicyNo, InvoiceNo, InvDate,
                     TermFrom, TermTo, ClientName, Comm%, CommAmt, NetDue

Importing is safe to repeat: clients are matched by name, policies by policy number and
commission lines by invoice number, so nothing is duplicated.
"""
import csv
import os
import re
from datetime import date, datetime, timedelta

from .db import execute, query

# Insurer class codes (invoice prefix) -> policy type.  Codes marked "inferred" were
# worked out from the reports rather than stated in them, and are noted on the policy.
CLASS_CODES = {
    "PCL": ("Motor - Private Car Comprehensive", True),
    "PCT": ("Motor - Private Car Third Party", False),
    "PCP": ("Motor - Private Car", True),
    "CGC": ("Motor - Commercial Comprehensive", True),
    "CGT": ("Motor - Commercial Third Party", False),
    "HOC": ("Fire - Homeowners / Comprehensive Building", False),
    "FC": ("Fire - Commercial", True),
    "CCT": ("Motor - Commercial Third Party", True),
    "PL": ("Public Liability", True),
    "WC": ("Workers' Compensation", True),
}

# The VSLOB column (line of business) is used when a class code isn't known yet.
LOB_TYPES = {"A": "Motor - Private Car", "F": "Fire - Homeowners / Comprehensive Building",
             "L": "General Liability"}

COVER_TYPES = [  # (text found in TYPE OF COVER, policy type)
    ("COMPREHENSIVE BLDG", "Fire - Homeowners / Comprehensive Building"),
    ("COMMERCIAL FIRE", "Fire - Commercial"),
    ("COMM OWN GOODS T P", "Motor - Commercial Third Party"),
    ("COMM OWN GOODS COMP", "Motor - Commercial Comprehensive"),
    ("PRIVATE CAR T P", "Motor - Private Car Third Party"),
    ("PRIVATE CAR COMP", "Motor - Private Car Comprehensive"),
]

BUSINESS_WORDS = {"LIMITED", "LTD", "LTD.", "TRADING", "COMPANY", "CO.", "INC", "INC.",
                  "PROPERTIES", "FINANCE", "ENTERPRISES", "SERVICES", "ASSOCIATES",
                  "HOLDINGS", "AGENCY", "CONTRACTORS", "GROUP", "STORES", "SUPPLIES",
                  "INVESTMENTS", "DEVELOPMENT", "CHURCH", "SCHOOL", "TRUST"}
TITLES = {"MR", "MRS", "MS", "MISS", "DR", "MDM", "MESSRS"}

MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"], 1)}


# ---------------------------------------------------------------------------
# Reading files
# ---------------------------------------------------------------------------
def read_workbook(path):
    """Return [(sheet_name, rows)] where rows are lists of cell values."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".xls":
        import xlrd
        book = xlrd.open_workbook(path)
        out = []
        for sh in book.sheets():
            rows = []
            for r in range(sh.nrows):
                row = []
                for c in range(sh.ncols):
                    cell = sh.cell(r, c)
                    v = cell.value
                    if cell.ctype == xlrd.XL_CELL_DATE:
                        v = xlrd.xldate_as_datetime(v, book.datemode).date()
                    row.append(v)
                rows.append(row)
            out.append((sh.name, rows))
        return out
    if ext in (".xlsx", ".xlsm"):
        import openpyxl
        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
        out = []
        for sh in book.worksheets:
            rows = [["" if v is None else v for v in row] for row in sh.iter_rows(values_only=True)]
            out.append((sh.title, rows))
        return out
    if ext == ".csv":
        with open(path, newline="", encoding="utf-8-sig") as f:
            return [(os.path.basename(path), list(csv.reader(f)))]
    raise ValueError(f"Unsupported file type '{ext}'. Use .xls, .xlsx or .csv.")


# ---------------------------------------------------------------------------
# Value cleaning
# ---------------------------------------------------------------------------
def norm_header(h):
    return re.sub(r"\s+", " ", str(h)).strip().upper()


def compact(h):
    """'Comm %' / 'COMM%' / 'Comm_%' -> 'COMM%'; 'Policy No.' -> 'POLICYNO'."""
    return re.sub(r"[^A-Z0-9%]", "", str(h).upper())


def money(v):
    return "?" if v is None else f"${v:,.2f}"


def pct(v):
    return "?" if v is None else f"{v:g}%"


def nice(d):
    return d.strftime("%d %b %Y") if d else "?"


def text(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return re.sub(r"\s+", " ", str(v)).strip()


def number(v):
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", "").replace("$", ""))
    except ValueError:
        return None


def to_date(v):
    """Accept 20261116 / 20261116.0 / date / '2026-11-16' / '16/11/2026'."""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = text(v)
    if not s:
        return None
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def iso(d):
    return d.isoformat() if d else None


def phone(v):
    digits = re.sub(r"\D", "", text(v))
    if not digits or set(digits) == {"0"}:
        return None
    if len(digits) == 7:
        return f"{digits[:3]}-{digits[3:]}"
    if len(digits) == 10 and digits.startswith("868"):
        return f"(868) {digits[3:6]}-{digits[6:]}"
    return digits


def policy_key(v):
    return text(v).upper()


def name_key(s):
    s = re.sub(r"[^A-Z0-9 ]", " ", text(s).upper())
    return " ".join(s.split())


def smart_title(s):
    out = []
    for w in text(s).split(" "):
        if w.upper() in ("LTD", "LTD.", "CO.", "INC."):
            out.append(w.title())
        else:
            out.append("-".join(p[:1].upper() + p[1:].lower() for p in w.split("-")))
    return " ".join(out)


def split_name(client_name, addressee=""):
    """Return (first_name, last_name). Businesses go entirely into last_name."""
    name = text(client_name)
    words = name.upper().replace(",", " ").split()
    addr_first = text(addressee).upper().replace(".", " ").split()[:1]
    is_person_title = bool(addr_first) and addr_first[0] in TITLES
    if any(w in BUSINESS_WORDS for w in words) and not is_person_title:
        return "", smart_title(name)
    parts = smart_title(name).split(" ")
    if len(parts) == 1:
        return "", parts[0]
    return " ".join(parts[:-1]), parts[-1]


def statement_month(sheet_name, filename, rows_dates):
    for src in (sheet_name, filename):
        m = re.search(r"(January|February|March|April|May|June|July|August|September|"
                      r"October|November|December)\D{0,3}(\d{4})", src or "", re.I)
        if m:
            return f"{m.group(2)}-{MONTHS[m.group(1).lower()]:02d}"
    if rows_dates:
        d = max(rows_dates)
        return f"{d.year}-{d.month:02d}"
    return None


# ---------------------------------------------------------------------------
# Parsing report layouts
# ---------------------------------------------------------------------------
COMMISSION_COLUMNS = {
    "prod": ("PRODCODE", "PRODUCERCODE", "AGENTCODE"),
    "prefix": ("INVPREFIX", "PREFIX", "CLASS", "CLASSCODE"),
    "policy": ("POLICYNO", "POLICYNUMBER", "POLICY", "POLNO"),
    "invoice": ("INVOICENO", "INVOICENUMBER", "INVNO", "INVOICE"),
    "invdate": ("INVDATE", "INVOICEDATE"),
    "from": ("TERMFROM", "FROM", "EFFECTIVEDATE", "STARTDATE"),
    "to": ("TERMTO", "TO", "EXPIRYDATE", "EXPIRATIONDATE", "ENDDATE"),
    "client": ("CLIENTNAME", "CLIENT", "INSURED", "INSUREDNAME", "NAME"),
    "rate": ("COMM%", "COMMISSION%", "COMMPCT", "COMMRATE", "COMMISSIONRATE", "RATE%", "RATE", "%"),
    "amount": ("COMMAMT", "COMMISSIONAMT", "COMMAMOUNT", "COMMISSIONAMOUNT", "COMMISSION", "COMM"),
    "net": ("NETDUE", "NETAMT", "NET"),
    "invtype": ("INVTYPE", "INVOICETYPE", "TYPE"),
    "lob": ("VSLOB", "LOB"),
    "premium": ("TOTPRM", "TOTPREM", "TOTALPREMIUM", "PREMIUM", "PREM", "GROSSPREMIUM"),
}


def detect(header):
    h = {compact(c) for c in header}
    has = lambda key: any(a in h for a in COMMISSION_COLUMNS[key])  # noqa: E731
    if has("policy") and has("client") and has("amount") and "RENEWALDATE" not in h:
        return "commission"
    if "POLICY" in h and "RENEWALDATE" in h:
        return "renewal"
    return None


def find_header(rows, limit=15):
    """Reports sometimes have a title line or two above the column headings."""
    for i, row in enumerate(rows[:limit]):
        kind = detect(row)
        if kind:
            return i, kind
    return None, None


def _col(header, *names, startswith=False):
    hs = [norm_header(c) for c in header]
    for n in names:
        for i, h in enumerate(hs):
            if h == n or (startswith and h.startswith(n)):
                return i
    return None


def parse_renewals(rows):
    header = rows[0]
    idx = {k: _col(header, *v, startswith=s) for k, (v, s) in {
        "lob": (("LOB",), False), "policy": (("POLICY",), False),
        "renewal": (("RENEWAL DATE",), False), "client": (("CLIENT NAME",), False),
        "addressee": (("ADDRESSEE",), False), "a1": (("ADDRESS 1",), False),
        "a2": (("ADDRESS 2",), False), "a3": (("ADDRESS 3",), False),
        "phone": (("PHONE",), False), "sum": (("SUM INSURED",), False),
        "gross": (("GROSS PREMIUM",), False), "addl": (("ADDIT. PREM.",), False),
        "net": (("NET PREM",), True), "os": (("O/S PREM",), False),
        "disc": (("DISCOUNTS",), False), "cover": (("TYPE OF COVER",), False),
        "product": (("MATE/MAX",), False), "risk": (("RISK",), False),
        "mortgagee": (("MORTGAGEE",), False), "remarks": (("REMARKS",), False),
    }.items()}

    def get(row, k):
        i = idx[k]
        return row[i] if i is not None and i < len(row) else ""

    out = []
    for row in rows[1:]:
        if not text(get(row, "policy")) or not text(get(row, "client")):
            continue
        out.append({
            "policy": policy_key(get(row, "policy")), "lob": text(get(row, "lob")),
            "renewal": to_date(get(row, "renewal")), "client": text(get(row, "client")),
            "addressee": text(get(row, "addressee")),
            "address": smart_title(text(get(row, "a1"))),
            "city": smart_title(", ".join(x for x in (text(get(row, "a2")).rstrip(","),
                                                      text(get(row, "a3")).rstrip(".")) if x)),
            "phone": phone(get(row, "phone")), "sum": number(get(row, "sum")),
            "gross": number(get(row, "gross")), "addl": number(get(row, "addl")),
            "net": number(get(row, "net")),
            "os": number(get(row, "os")) if idx["os"] is not None else None,
            "disc": text(get(row, "disc")), "cover": text(get(row, "cover")),
            "product": text(get(row, "product")), "risk": text(get(row, "risk")),
            "mortgagee": text(get(row, "mortgagee")), "remarks": text(get(row, "remarks")),
        })
    return out


def merge_renewals(records):
    """The same policy can appear on several sheets; combine, preferring filled-in values."""
    merged = {}
    for r in records:
        cur = merged.setdefault(r["policy"], {})
        for k, v in r.items():
            if k == "net":
                if v and not cur.get("net"):
                    cur["net"] = v
            elif k == "os":
                if v is not None and cur.get("os") is None:
                    cur["os"] = v
            elif v not in (None, "") and cur.get(k) in (None, ""):
                cur[k] = v
            else:
                cur.setdefault(k, v)
    return list(merged.values())


def parse_commissions(rows):
    header = [compact(h) for h in rows[0]]
    c = {}
    for key, aliases in COMMISSION_COLUMNS.items():
        c[key] = next((header.index(a) for a in aliases if a in header), None)

    def get(row, k):
        i = c[k]
        return row[i] if i is not None and i < len(row) else ""

    out = []
    for row in rows[1:]:
        if not text(get(row, "policy")) or not text(get(row, "client")):
            continue  # skips the totals row
        if compact(get(row, "policy")) in ("POLICYNO", "POLICY"):
            continue  # a repeated heading row
        out.append({
            "prod": text(get(row, "prod")), "prefix": text(get(row, "prefix")).upper(),
            "policy": policy_key(get(row, "policy")), "invoice": text(get(row, "invoice")),
            "invdate": to_date(get(row, "invdate")), "from": to_date(get(row, "from")),
            "to": to_date(get(row, "to")), "client": text(get(row, "client")),
            "rate": number(get(row, "rate")), "amount": number(get(row, "amount")) or 0.0,
            "invtype": text(get(row, "invtype")), "lob": text(get(row, "lob")).upper(),
            "premium": number(get(row, "premium")),
        })
        rec = out[-1]
        if not rec["invoice"]:  # no invoice number: build a stable key so re-imports still match
            rec["invoice"] = (f"{rec['policy']}-{iso(rec['invdate']) or iso(rec['from']) or ''}-"
                              f"{rec['amount']:.2f}")
        if rec["rate"] is None and rec["premium"] and rec["amount"]:
            rec["rate"] = round(rec["amount"] / rec["premium"] * 100, 2)  # work the rate out
    return out


# ---------------------------------------------------------------------------
# Writing to the database
# ---------------------------------------------------------------------------
class Report:
    def __init__(self):
        self.lines = []   # (kind, message, link-endpoint, id)
        self.counts = {}

    def add(self, kind, msg, endpoint=None, obj_id=None):
        self.lines.append((kind, msg, endpoint, obj_id))
        self.counts[kind] = self.counts.get(kind, 0) + 1


def _find_client(name):
    key = name_key(name)
    for c in query("SELECT * FROM clients"):
        if name_key(f"{c['first_name']} {c['last_name']}") == key:
            return c
    return None


def get_or_create_client(report, name, addressee="", address="", city="", phone_no=None):
    c = _find_client(name)
    if c is None:
        first, last = split_name(name, addressee)
        cid = execute("INSERT INTO clients (first_name, last_name, address, city, phone) "
                      "VALUES (?, ?, ?, ?, ?)", (first, last, address or None, city or None, phone_no))
        report.add("Client added", f"{(first + ' ' + last).strip()}", "client_detail", cid)
        return cid
    fills = {k: v for k, v in (("address", address), ("city", city), ("phone", phone_no))
             if v and not c[k]}
    if fills:
        execute(f"UPDATE clients SET {', '.join(k + ' = ?' for k in fills)} WHERE id = ?",
                (*fills.values(), c["id"]))
        report.add("Client updated", f"{(c['first_name'] + ' ' + c['last_name']).strip()}: added "
                   + ", ".join(fills), "client_detail", c["id"])
    return c["id"]


def _core_number(number):
    """'PCP 587 368 000' -> '587368'; '587368' -> '587368'."""
    digits = re.sub(r"\D", "", number or "")
    if len(digits) > 6 and digits.endswith("000"):
        digits = digits[:-3]
    return digits.lstrip("0")


def _find_policy(number):
    row = query("SELECT * FROM policies WHERE UPPER(TRIM(policy_number)) = ?", (number,), one=True)
    if row is not None:
        return row
    core = _core_number(number)
    if len(core) < 4:
        return None
    for row in query("SELECT * FROM policies WHERE policy_number IS NOT NULL"):
        if _core_number(row["policy_number"]) == core:
            return row
    return None


def _append_note(existing, line):
    existing = existing or ""
    if line in existing:
        return existing
    return (existing + "\n" + line).strip()


def _cover_type(cover, lob):
    cu = cover.upper()
    for needle, ptype in COVER_TYPES:
        if needle in cu:
            return ptype
    if lob.upper() == "F":
        return "Fire - Commercial" if "COMM" in cu else "Fire - Homeowners / Comprehensive Building"
    if lob.upper() == "A":
        return "Motor - Private Car"
    return "Other"


def apply_renewals(records, carrier, source, report):
    from .app import today as _today
    today = _today()
    for r in merge_renewals(records):
        cid = get_or_create_client(report, r["client"], r.get("addressee"), r.get("address"),
                                   r.get("city"), r.get("phone"))
        pol = _find_policy(r["policy"])
        renewal = r.get("renewal")
        net = r.get("net") or r.get("gross")
        net = round(net, 2) if net else None
        note_bits = [f"{source}:"]
        if r.get("cover"):
            note_bits.append(f"Cover: {r['cover']}.")
        if r.get("product"):
            note_bits.append(f"Product: {r['product']}.")
        if r.get("gross") is not None:
            note_bits.append(f"Gross premium ${r['gross']:,.2f}.")
        if r.get("addl"):
            note_bits.append(f"Additional premium ${r['addl']:,.2f}.")
        if r.get("net"):
            note_bits.append(f"Net premium incl. tax ${r['net']:,.2f}.")
        if r.get("os"):
            note_bits.append(f"O/S premium shown on listing: ${r['os']:,.2f}.")
        if r.get("disc"):
            note_bits.append(f"Discounts: {r['disc']}.")
        if r.get("mortgagee"):
            note_bits.append(f"Mortgagee: {r['mortgagee']}.")
        if r.get("remarks"):
            note_bits.append(f"REMARKS: {r['remarks']}")
        note = " ".join(note_bits)
        coverage = r["sum"] if r.get("sum") and r["sum"] > 100 else None

        if pol is None:
            prefix = re.match(r"^([A-Z]{2,3})\s", r["policy"])
            ptype = (CLASS_CODES[prefix.group(1)][0] if prefix and prefix.group(1) in CLASS_CODES
                     else _cover_type(r.get("cover", ""), r.get("lob", "")))
            pid = execute("""INSERT INTO policies (client_id, policy_number, policy_type, carrier,
                    status, expiration_date, premium, payment_frequency, next_due_date,
                    coverage_amount, insured_item, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (cid, r["policy"], ptype, carrier or None, "Active", iso(renewal), net,
                           "Annual", iso(renewal), coverage, r.get("risk") or None, note))
            report.add("Policy added", f"#{r['policy']} {ptype} – {r['client'].title()}"
                       + (f", renews {renewal:%d %b %Y}" if renewal else ""), "policy_detail", pid)
        else:
            pid = pol["id"]
            upd = {"notes": _append_note(pol["notes"], note)}
            already_renewed = (renewal and pol["expiration_date"]
                               and pol["expiration_date"] > iso(renewal))
            if renewal and not already_renewed:
                upd["expiration_date"] = iso(renewal)
                upd["next_due_date"] = iso(renewal)
                if net:
                    upd["premium"] = net
                upd["payment_frequency"] = pol["payment_frequency"] or "Annual"
            for k, v in (("coverage_amount", coverage), ("insured_item", r.get("risk")),
                         ("carrier", carrier)):
                if v and not pol[k]:
                    upd[k] = v
            if pol["policy_type"] in (None, "", "Other"):
                upd["policy_type"] = _cover_type(r.get("cover", ""), r.get("lob", ""))
            execute(f"UPDATE policies SET {', '.join(k + ' = ?' for k in upd)} WHERE id = ?",
                    (*upd.values(), pid))
            msg = f"#{r['policy']} – {r['client'].title()}"
            if already_renewed:
                msg += f": already renewed to {pol['expiration_date']} (no payment reminder set)"
            elif renewal:
                msg += f": renewal/payment due {renewal:%d %b %Y}"
            report.add("Policy updated", msg, "policy_detail", pid)

        # Outstanding premium -> a to-do reminder so the client is chased.
        remarks = (r.get("remarks") or "").upper()
        if "OUTSTANDING" in remarks or (r.get("os") or 0) > 0:
            title = f"Collect outstanding premium – policy {r['policy']}"
            exists = query("SELECT id FROM reminders WHERE title = ? AND done = 0", (title,), one=True)
            if not exists:
                execute("INSERT INTO reminders (title, due_date, client_id, policy_id, notes) "
                        "VALUES (?,?,?,?,?)", (title, today.isoformat(), cid, pid,
                                               r.get("remarks") or f"O/S premium {money(r.get('os'))}"))
                if not renewal:
                    execute("UPDATE policies SET next_due_date = ? WHERE id = ? AND next_due_date IS NULL",
                            (today.isoformat(), pid))
                report.add("Reminder added", f"{r['client'].title()}: {r.get('remarks') or title}",
                           "client_detail", cid)


def apply_commissions(records, carrier, source, month, report):
    for r in records:
        if query("SELECT id FROM commissions WHERE invoice_no = ?", (r["invoice"],), one=True):
            report.add("Already imported", f"Invoice {r['invoice']} ({r['client'].title()}) skipped")
            continue
        cid = get_or_create_client(report, r["client"])
        pol = _find_policy(r["policy"])
        code_type, inferred = CLASS_CODES.get(
            r["prefix"], (LOB_TYPES.get(r.get("lob", ""), "Other"), True))
        if r.get("premium") is not None and r["premium"] > 0:
            basis = round(r["premium"], 2)       # premium stated on the statement
        elif r.get("premium") is None and r["rate"]:
            basis = round(r["amount"] / r["rate"] * 100, 2)   # no premium column: work it out
        else:
            basis = None    # zero/negative premium = adjustment, don't guess a premium
        term = f"{nice(r['from'])} – {nice(r['to'])}"
        note = (f"{source}: invoice {r['invoice'] or '?'} ({r['prefix'] or '?'}), term {term}, "
                f"commission {pct(r['rate'])} = {money(r['amount'])}"
                + (f" on premium {money(basis)}." if basis else "."))
        if r.get("premium") is not None and r["premium"] <= 0:
            note += (f" Statement shows premium {money(r['premium'])} (adjustment/return premium, "
                     f"invoice type {r.get('invtype') or '?'}) – please check.")
        if inferred:
            note += f" Policy type inferred from insurer class code '{r['prefix']}' – please confirm."

        if pol is None:
            pid = execute("""INSERT INTO policies (client_id, policy_number, policy_type, carrier,
                    status, effective_date, expiration_date, premium, payment_frequency,
                    commission_rate, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                          (cid, r["policy"], code_type, carrier or None, "Active", iso(r["from"]),
                           iso(r["to"]), basis, "Annual", r["rate"], note))
            report.add("Policy added", f"#{r['policy']} {code_type} – {r['client'].title()}"
                       + (f", term to {nice(r['to'])}" if r["to"] else ""), "policy_detail", pid)
        else:
            pid = pol["id"]
            upd = {"notes": _append_note(pol["notes"], note)}
            if r["rate"] is not None:
                upd["commission_rate"] = r["rate"]
            if r["to"] and (not pol["expiration_date"] or iso(r["to"]) > pol["expiration_date"]):
                upd["expiration_date"] = iso(r["to"])
                upd["effective_date"] = iso(r["from"])
                if basis and (not pol["premium"] or (r.get("premium") or 0) > 0):
                    upd["premium"] = basis
            # Commission paid on a renewal term means that renewal was invoiced and paid:
            # clear the payment reminder that a renewal listing may have set.
            due = pol["next_due_date"]
            if (due and r["from"] and r["to"] and (r.get("premium") is None or r["premium"] > 0)
                    and iso(r["from"] - timedelta(days=31)) <= due <= iso(r["from"] + timedelta(days=31))):
                upd["next_due_date"] = None
            if not pol["carrier"] and carrier:
                upd["carrier"] = carrier
            generic = {None, "", "Other", "Motor - Private Car", "General Liability"}
            if pol["policy_type"] in generic and code_type not in generic:
                upd["policy_type"] = code_type
            execute(f"UPDATE policies SET {', '.join(k + ' = ?' for k in upd)} WHERE id = ?",
                    (*upd.values(), pid))
            msg = f"#{r['policy']} – {r['client'].title()}: commission {pct(r['rate'])} recorded"
            if "next_due_date" in upd:
                msg += f"; renewal from {nice(r['from'])} already invoiced, payment reminder cleared"
            report.add("Policy updated", msg, "policy_detail", pid)

        execute("""INSERT INTO commissions (policy_id, client_name, statement_month, invoice_no,
                   class_code, invoice_date, term_from, term_to, rate, amount, producer_code,
                   source_file, premium, inv_type) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (pid, r["client"].title(), month, r["invoice"], r["prefix"], iso(r["invdate"]),
                 iso(r["from"]), iso(r["to"]), r["rate"], r["amount"], r["prod"], source,
                 r.get("premium"), r.get("invtype") or None))
        report.add("Commission added", f"{r['client'].title()} – invoice {r['invoice']}: "
                   f"{money(r['amount'])}", "policy_detail", pid)


def import_file(path, carrier="", display_name=None, report=None):
    """Import one spreadsheet. Must run inside a Flask app context."""
    report = report or Report()
    name = display_name or os.path.basename(path)
    found = False
    renewal_rows = []
    for sheet, rows in read_workbook(path):
        rows = [r for r in rows if any(text(v) for v in r)]
        if not rows:
            continue
        start, kind = find_header(rows)
        if kind:
            rows = rows[start:]
        if kind == "renewal":
            found = True
            renewal_rows.extend(parse_renewals(rows))
        elif kind == "commission":
            found = True
            recs = parse_commissions(rows)
            month = statement_month(sheet, name, [r["invdate"] for r in recs if r["invdate"]])
            apply_commissions(recs, carrier, f"Commission statement {month or name}", month, report)
    if renewal_rows:
        apply_renewals(renewal_rows, carrier, f"Renewal listing ({name})", report)
    if not found:
        report.add("Warning", f"{name}: no renewal listing or commission statement layout recognised.")
    return report
