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
    assert b"First name is required" in r.data
    cid = make_client(client)
    make_policy(client, cid)
    client.post(f"/clients/{cid}/delete")
    assert b"No policies match" in client.get("/policies").data
