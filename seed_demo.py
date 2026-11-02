"""Fill a database with sample clients, policies and meetings so you can try the app.

Usage:  python seed_demo.py            (writes to insurance.db)
        TRACKER_DB=demo.db python seed_demo.py
Only run this on an empty/test database - it adds fake records.
"""
from datetime import date, timedelta

from insurance_tracker.app import create_app
from insurance_tracker.app import insert

app = create_app()
t = date.today()
d = lambda n: (t + timedelta(days=n)).isoformat()

with app.app_context():
    c1 = insert("clients", {"first_name": "Maria", "last_name": "Lopez", "phone": "(555) 201-3344",
                            "email": "maria.lopez@example.com", "city": "Austin", "state": "TX",
                            "preferred_contact": "Text"})
    c2 = insert("clients", {"first_name": "James", "last_name": "Carter", "phone": "(555) 778-1020",
                            "email": "jcarter@example.com", "city": "Round Rock", "state": "TX"})
    c3 = insert("clients", {"first_name": "Priya", "last_name": "Shah", "phone": "(555) 432-9090",
                            "email": "priya@shahbakery.example", "city": "Austin", "state": "TX",
                            "notes": "Owns Shah Bakery - 6 employees."})
    insert("policies", {"client_id": c1, "policy_type": "Auto", "carrier": "Lone Star Mutual",
                        "policy_number": "AU-55821", "status": "Active", "premium": 142.0,
                        "payment_frequency": "Monthly", "next_due_date": d(-4),
                        "effective_date": d(-200), "expiration_date": d(165), "deductible": 500,
                        "coverage_amount": 100000, "commission_rate": 10,
                        "insured_item": "2022 Toyota RAV4 - VIN 2T3P1RFV0NW000000"})
    insert("policies", {"client_id": c1, "policy_type": "Homeowners", "carrier": "Hearth Insurance",
                        "policy_number": "HO-90210", "status": "Active", "premium": 1680.0,
                        "payment_frequency": "Annual", "next_due_date": d(40),
                        "expiration_date": d(40), "coverage_amount": 385000, "deductible": 2500,
                        "commission_rate": 12, "insured_item": "1420 Oak Hollow Dr, Austin TX"})
    insert("policies", {"client_id": c2, "policy_type": "Life - Term", "carrier": "Evergreen Life",
                        "policy_number": "TL-30-7781", "status": "Active", "premium": 48.75,
                        "payment_frequency": "Monthly", "next_due_date": d(3),
                        "expiration_date": d(3650), "coverage_amount": 500000, "commission_rate": 50,
                        "beneficiaries": "Angela Carter (spouse) 100%"})
    insert("policies", {"client_id": c3, "policy_type": "Business Owners (BOP)", "carrier": "Main Street Commercial",
                        "policy_number": "BOP-4410", "status": "Active", "premium": 610.0,
                        "payment_frequency": "Quarterly", "next_due_date": d(-12),
                        "expiration_date": d(25), "coverage_amount": 1000000, "commission_rate": 15})
    insert("policies", {"client_id": c3, "policy_type": "Workers' Compensation", "carrier": "Main Street Commercial",
                        "status": "Quoted", "premium": 2200.0, "payment_frequency": "Annual"})
    insert("meetings", {"title": "Renewal review", "client_id": c3, "meeting_type": "Renewal Review",
                        "date": d(1), "start_time": "10:00", "end_time": "11:00", "location": "Shah Bakery"})
    insert("meetings", {"title": "Life insurance quote", "client_id": c2, "meeting_type": "New Quote",
                        "date": d(4), "start_time": "14:30", "location": "Zoom"})
    insert("meetings", {"title": "Team meeting", "meeting_type": "Internal / Team", "date": d(2),
                        "start_time": "09:00", "location": "Office"})
    insert("reminders", {"title": "Send umbrella quote", "due_date": d(0), "client_id": c1})
    insert("reminders", {"title": "Follow up on workers' comp quote", "due_date": d(5), "client_id": c3})
print("Demo data added.")
