import pytest

from insurance_tracker.app import add_months, create_app
from datetime import date


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKER_TODAY", "2026-11-02")
    app = create_app(str(tmp_path / "test.db"))
    app.config["TESTING"] = True
    return app.test_client()


def make_client(c, **kw):
    data = {"first_name": "Maria", "last_name": "Lopez", "email": "maria@example.com", "phone": "555-1234"}
    data.update(kw)
    r = c.post("/clients/new", data=data)
    assert r.status_code == 302
    return int(r.headers["Location"].rstrip("/").split("/")[-1])


def make_policy(c, client_id, **kw):
    data = {"client_id": client_id, "policy_type": "Auto", "status": "Active", "carrier": "Acme Mutual",
            "policy_number": "AU-100", "premium": "125.50", "payment_frequency": "Monthly",
            "next_due_date": "2026-10-28", "expiration_date": "2026-12-15", "commission_rate": "10"}
    data.update(kw)
    r = c.post("/policies/new", data=data)
    assert r.status_code == 302
    return int(r.headers["Location"].split("/")[-1])


def test_add_months_clamps_month_end():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2026, 11, 15), 3) == date(2027, 2, 15)


def test_all_pages_render(client):
    cid = make_client(client)
    pid = make_policy(client, cid)
    client.post("/meetings/new", data={"title": "Annual review", "date": "2026-11-05",
                                       "start_time": "10:00", "client_id": cid})
    for url in ["/", "/clients", f"/clients/{cid}", "/policies", f"/policies/{pid}",
                f"/policies/{pid}/edit", "/reminders", "/calendar", "/meetings",
                "/meetings/new", "/clients/new", "/policies/new", "/search?q=Lopez"]:
        r = client.get(url)
        assert r.status_code == 200, url


def test_overdue_payment_shows_and_payment_advances_due_date(client):
    cid = make_client(client)
    pid = make_policy(client, cid)
    html = client.get("/").get_data(as_text=True)
    assert "5 days overdue" in html
    assert "mailto:maria@example.com" in html

    r = client.post(f"/policies/{pid}/payments",
                    data={"paid_on": "2026-11-02", "amount": "125.50", "advance": "1"},
                    follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "Next payment due Nov 28, 2026" in html
    assert "No overdue payments" in client.get("/").get_data(as_text=True)


def test_renewal_and_calendar_events(client):
    cid = make_client(client)
    make_policy(client, cid, next_due_date="2026-11-20")
    client.post("/meetings/new", data={"title": "Quote meeting", "date": "2026-11-10", "start_time": "14:30"})
    html = client.get("/calendar?year=2026&month=11").get_data(as_text=True)
    assert "Quote meeting" in html and "2:30 PM" in html
    assert "Payment due: Maria Lopez" in html
    dec = client.get("/calendar?year=2026&month=12").get_data(as_text=True)
    assert "Renewal: Maria Lopez" in dec
    assert "Renewals in the next 60 days" in client.get("/reminders").get_data(as_text=True)


def test_task_reminders_toggle(client):
    client.post("/reminders", data={"title": "Call carrier about claim", "due_date": "2026-11-01"})
    html = client.get("/").get_data(as_text=True)
    assert "Call carrier about claim" in html and "1 day overdue" in html
    client.post("/reminders/1/toggle")
    assert "Call carrier about claim" not in client.get("/").get_data(as_text=True)


def test_ics_export(client):
    client.post("/meetings/new", data={"title": "Review, home", "date": "2026-11-10", "start_time": "09:00"})
    r = client.get("/meetings/1.ics")
    body = r.get_data(as_text=True)
    assert r.mimetype == "text/calendar"
    assert "DTSTART:20261110T090000" in body and "DTEND:20261110T100000" in body
    assert "SUMMARY:Review\\, home" in body


def test_validation_and_cascade_delete(client):
    r = client.post("/clients/new", data={"first_name": ""})
    assert b"Last name / business name is required" in r.data
    cid = make_client(client)
    make_policy(client, cid)
    client.post(f"/clients/{cid}/delete")
    assert b"No policies match" in client.get("/policies").data


RENEWAL_CSV = """LOB,POLICY,RENEWAL DATE,CLIENT NAME,ADDRESSEE,ADDRESS 1 ,ADDRESS 2,ADDRESS 3,PHONE ,SUM INSURED,GROSS PREMIUM,ADDIT. PREM.,NET PREM includes tax,DISCOUNTS,TYPE OF COVER,MATE/MAX,RISK,MORTGAGEE,REMARKS
F,11111,20261121,ACME HOLDINGS LIMITED,ACME HOLDINGS LIMITED,#1 MAIN STREET,SAN FERNANDO,,6221234,1000000,4000,0,4240.5555,,COMPREHENSIVE BLDG - FIRE,,PDH $1M,,
A,22222,20261115,JOHN Q. PUBLIC,MR. JOHN Q. PUBLIC,#2 SIDE ROAD,ARIMA,,0,100,2000,0,1500,45.00% N.C.D.,PRIVATE CAR T P ONLY,Automax,PAB-1234 NISSAN TIIDA,,
,PCP 999 000 000,,JANE DOE,,,,,,,,,,,,,PEF-0001,,POLICY HAS AN OUTSTANDING PREMIUM. NO RENEWAL TERMS.
"""

COMMISSION_CSV = """ProdCode,InvPrefix,PolicyNo,InvoiceNo,InvDate,TermFrom,TermTo,ClientName,Comm%,CommAmt,NetDue
4815,HOC,11111,900001,20261029,20261121,20271120,ACME HOLDINGS LIMITED,12.5,500,-500
4815,PCL,33333,900002,20261001,20261010,20271009,MARY  SMITH,15,300,-300
,,,,,,,,,800,-800
"""


def upload(client, name, body):
    import io
    return client.post("/import", data={"carrier": "Test Insurer",
                                        "files": [(io.BytesIO(body.encode()), name)]},
                       content_type="multipart/form-data")


def test_import_renewal_and_commission(client):
    r = upload(client, "Commissions for October 2026.csv", COMMISSION_CSV)
    assert r.status_code == 200 and b"Commission added" in r.data
    r = upload(client, "November 2026 Renewal Listing.csv", RENEWAL_CSV)
    html = r.get_data(as_text=True)
    assert "already renewed to 2027-11-20" in html           # commission showed it renewed
    assert "OUTSTANDING PREMIUM" in html                    # reminder created

    clients = client.get("/clients").get_data(as_text=True)
    assert "Acme Holdings Limited" in clients and "Public, John Q." in clients
    assert "Acme Holdings Limited," not in clients          # business: no trailing comma
    reminders = client.get("/reminders").get_data(as_text=True)
    assert "Collect outstanding premium" in reminders
    assert "Nov 15, 2026" in reminders                       # John Q. Public renewal payment due
    comm = client.get("/commissions").get_data(as_text=True)
    assert "October 2026" in comm and "$800.00" in comm

    # Importing again must not duplicate anything.
    r = upload(client, "Commissions for October 2026.csv", COMMISSION_CSV)
    assert b"Already imported" in r.data and b"Commission added" not in r.data
    r = upload(client, "November 2026 Renewal Listing.csv", RENEWAL_CSV)
    assert b"Policy added" not in r.data and b"Client added" not in r.data
    assert client.get("/policies").get_data(as_text=True).count("Acme Holdings Limited") == 1


def test_password_protection(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKER_PASSWORD", "s3cret-pass")
    app = create_app(str(tmp_path / "pw.db"))
    c = app.test_client()
    r = c.get("/clients")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    assert c.get("/backup").status_code == 302
    assert c.get("/static/style.css").status_code == 200
    r = c.post("/login?next=/clients", data={"password": "wrong"})
    assert b"Wrong password" in r.data
    r = c.post("/login?next=//evil.example", data={"password": "s3cret-pass"})
    assert r.headers["Location"] == "/"
    assert c.get("/clients").status_code == 200
    r = c.get("/backup")
    assert r.status_code == 200 and r.data[:15] == b"SQLite format 3"
    c.post("/logout")
    assert c.get("/clients").status_code == 302


def test_failed_import_saves_nothing_and_explains(client, monkeypatch):
    from insurance_tracker import importer
    real = importer.apply_renewals

    def explode(records, carrier, source, report):
        real(records[:1], carrier, source, report)   # write something, then fail
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(importer, "apply_renewals", explode)
    r = upload(client, "November 2026 Renewal Listing.csv", RENEWAL_CSV)
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "was NOT imported" in html and "simulated failure" in html
    assert "No clients yet" in client.get("/clients").get_data(as_text=True)  # rolled back


def test_unexpected_error_shows_message(tmp_path):
    app = create_app(str(tmp_path / "e.db"))

    @app.route("/boom")
    def boom():
        raise ValueError("kaboom")

    r = app.test_client().get("/boom")
    assert r.status_code == 500 and b"ValueError: kaboom" in r.data



def test_import_xlsx_commission_with_premium_column(client, tmp_path):
    import io
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Agent"
    ws.append(["ProdCode", "InvPrefix", "PolicyNo", "InvoiceNo", "InvType", "InvDate", "TermFrom",
               "TermTo", "ClientName", "VSLOB", "Comm%", "TotPrm", "CommAmt", "NetDue"])
    ws.append([4815, "PCL", 999000, 700001, 6, 20260721, 20260717, 20261123, "JANE DOE", "A",
               15, -4444.05, 2320.73, -2320.73])
    ws.append([4815, "PL", 270928, 700002, 1, 20260826, 20260724, 20270723, "WIDGET LIMITED", "L",
               15, 800, 116.8, -116.8])
    ws.append([4815, "ZZ", 270930, 700003, 1, 20260826, 20260724, 20270723, "WIDGET LIMITED", "F",
               15, 1000, 150, -150])
    ws.append([None] * 11 + [None, 2587.53, -2587.53])
    buf = io.BytesIO()
    wb.save(buf)
    # The renewal listing knows Jane's policy as "PCP 999 000 000".
    upload(client, "November 2026 Renewal Listing.csv", RENEWAL_CSV)
    r = client.post("/import", data={"carrier": "", "files": [(io.BytesIO(buf.getvalue()),
                    "Commissions payable for August 2026.xlsx")]}, content_type="multipart/form-data")
    html = r.get_data(as_text=True)
    assert "NOT imported" not in html
    assert "#999000 – Jane Doe: commission 15% recorded" in html   # matched PCP 999 000 000
    comm = client.get("/commissions?month=2026-08").get_data(as_text=True)
    assert "August 2026" in comm and "$2,587.53" in comm and "$800.00" in comm
    policies = client.get("/policies").get_data(as_text=True)
    assert "Public Liability" in policies                # PL code
    assert "Fire - Homeowners" in policies              # unknown code falls back to VSLOB "F"
    assert policies.count("Doe, Jane") == 1
    # Jane still owes the outstanding premium: reminder and payment due survive the commission.
    rem = client.get("/reminders").get_data(as_text=True)
    assert "Collect outstanding premium" in rem and "Jane Doe" in rem
