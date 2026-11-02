# Broker Desk: Insurance Policy Tracker

A private web app for an insurance broker. It runs on your own computer and stores
everything in one SQLite database file (`insurance.db`).

## What it does

- **Clients**: name, phone(s), email, preferred contact method, address, date of birth, notes.
- **Policies**: linked to a client. Records policy type (Auto, Home, Renters, Life, Health,
  Medicare, Disability, Umbrella, Commercial / BOP, Workers' Comp, E&O, Cyber, Flood, and more),
  carrier, policy number, status (Active / Pending / Quoted / Lapsed / Cancelled / Expired),
  effective and expiration dates, premium, payment frequency, next payment due date,
  payment method, coverage limit, deductible, commission %, insured item (vehicle / property /
  business), beneficiaries or additional insureds, and notes.
- **Payments**: record each payment on a policy. The next due date moves forward automatically
  (monthly, quarterly, semi-annual or annual), and the full payment history is kept.
- **Reminders**:
  - **Overdue payments**: clients whose payment date has passed. They're highlighted in red,
    and a count shows in the menu bar.
  - **Payments coming due** (7 to 90 days ahead).
  - **Renewals** coming up in the next 60 days, with a button to schedule a review meeting.
  - **One-click email reminder**: opens your email program with a payment or renewal message
    already written to the client.
  - **To-do reminders**: your own follow-ups ("call about life quote"), optionally linked to a client.
- **Meeting calendar**: a month view showing your meetings along with payment due dates,
  renewals and to-dos. Click any day to add a meeting. Meetings have a type (client meeting,
  policy review, renewal, quote, claim help, internal/team, and more), client, time, location and notes.
  Each meeting can be downloaded as an `.ics` file to add to your phone, Outlook or Google Calendar
  (with a 30-minute alert).
- **Dashboard**: totals (clients, active policies, annual premium, estimated commission),
  overdue and upcoming payments, this week's meetings and to-dos, and upcoming renewals.
- **Search**: by client name, phone, email, policy number, carrier or insured item.

## Running it

You need Python 3.10 or newer.

```bash
pip install -r requirements.txt
```

```bash
python run.py
```

Then open http://127.0.0.1:5000 in your browser.

To try it out with sample data first, use a separate demo database:

```bash
TRACKER_DB=demo.db python seed_demo.py
```

```bash
TRACKER_DB=demo.db python run.py
```

## Backups

All your data is in `insurance.db`. Copy that file somewhere safe (for example a USB drive
or cloud folder) to back it up. It is excluded from git so client information is never committed.

## Privacy note

The app only listens on your own computer (`127.0.0.1`) and has no login. Don't put it on a
shared or public server without adding authentication first. Client records hold personal information.

## Tests

```bash
pip install pytest && python -m pytest
```
