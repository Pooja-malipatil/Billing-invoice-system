# BillDesk — Billing & Invoice Management System

A small full-stack billing app: manage customers, create multi-line-item
invoices, track payment status, and see revenue/pending/overdue numbers
on a dashboard.

Built as a portfolio project for a payment-solutions company interview —
the goal was to demonstrate relational schema design, backend business
logic (totals, status transitions), and a clean REST API, without
leaning on any frontend framework.

## Tech stack

| Layer     | Technology                          |
|-----------|--------------------------------------|
| Backend   | Python 3 + Flask (REST API + page routes) |
| Database  | SQLite (via Python's built-in `sqlite3`) |
| Frontend  | Plain HTML, CSS, JavaScript (`fetch` API, no framework) |

## Features

- Add / edit / delete customers
- Create invoices linked to a customer, with any number of line items
  (item name, quantity, price)
- Subtotal, tax, and total are calculated automatically from line items
- Invoice status: `Paid`, `Pending`, `Overdue`
  - Any `Pending` invoice past its due date is automatically flipped to
    `Overdue` whenever invoice data is read (no cron job needed)
  - One-click "Mark Paid" action on the invoices list
- Dashboard: total revenue (sum of paid invoices), total pending amount,
  count of overdue invoices, overdue amount, total invoice count
- Search invoices by customer name and/or filter by status

## Database schema

```
customers                    invoices                     invoice_items
─────────                    ────────                     ─────────────
id (PK)          ┐            id (PK)                       id (PK)
name                          customer_id (FK) ──────────┐   invoice_id (FK) ──┐
email                         invoice_date                │  item_name         │
phone                         due_date                    │  quantity          │
address                       tax_percent                 │  price             │
created_at                    subtotal                    │                    │
                               tax_amount                  │                    │
                               total                        │                    │
                               status                       │                    │
                               created_at                    │                    │
                                                              └── references ────┘
```

- `customers (1) ── (many) invoices` via `invoices.customer_id`
- `invoices (1) ── (many) invoice_items` via `invoice_items.invoice_id`
- `invoice_items` cascades on invoice delete (`ON DELETE CASCADE`) —
  deleting an invoice always removes its own line items.
- `invoices → customers` does **not** cascade — deleting a customer who
  has invoices is blocked at the database level, so you can't silently
  lose billing history. The API turns that low-level `IntegrityError`
  into a friendly 400 response.
- `subtotal` / `tax_amount` / `total` are stored on the `invoices` row
  rather than computed on every read. This is a deliberate
  denormalization: the dashboard needs to `SUM(total)` across many
  invoices, and recalculating from `invoice_items` on every dashboard
  load would mean a join + aggregate every time. The trade-off is that
  the app (in `recalculate_totals()` in `app.py`) must recompute and
  rewrite these columns whenever line items change — which it does, in
  one place, on both create and update.

## How to run locally

**1. Clone / download the project, then create a virtual environment (optional but recommended):**
```bash
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
```

**2. Install dependencies:**
```bash
pip install -r requirements.txt
```

**3. Run the app:**
```bash
python app.py
```
The database file `billing.db` is created automatically on first run
(via `schema.sql`) — no manual setup needed.

**4. Open the app:**
Visit `http://127.0.0.1:5000` in your browser.

To start over with an empty database, just delete `billing.db` and
restart the app.

## Project structure

```
billing-invoice-system/
├── app.py                  # Flask routes + REST API
├── schema.sql               # Database schema (3 related tables)
├── requirements.txt
├── static/
│   ├── css/style.css
│   └── js/
│       ├── common.js         # fetch wrapper, toast, currency formatting
│       ├── dashboard.js
│       ├── customers.js
│       ├── invoices.js
│       └── invoice_form.js
└── templates/
    ├── base.html             # shared layout/navbar
    ├── dashboard.html
    ├── customers.html
    ├── invoices.html
    └── invoice_form.html     # shared by "new" and "edit"
```

## API reference

| Method | Endpoint                         | Description                       |
|--------|-----------------------------------|------------------------------------|
| GET    | `/api/customers`                  | List all customers                 |
| POST   | `/api/customers`                  | Create a customer                  |
| PUT    | `/api/customers/<id>`             | Update a customer                  |
| DELETE | `/api/customers/<id>`             | Delete a customer (blocked if they have invoices) |
| GET    | `/api/invoices?search=&status=`   | List invoices, with optional filters |
| GET    | `/api/invoices/<id>`              | Get one invoice with its line items |
| POST   | `/api/invoices`                   | Create an invoice with line items  |
| PUT    | `/api/invoices/<id>`              | Update an invoice and its line items |
| PATCH  | `/api/invoices/<id>/status`       | Quick status update (e.g. "Mark Paid") |
| DELETE | `/api/invoices/<id>`              | Delete an invoice (and its line items) |
| GET    | `/api/dashboard`                  | Aggregate stats for the dashboard  |

## Ideas for extending this project

A few small features to add yourself, so you can genuinely explain
customizations you made beyond the base spec:

1. **PDF invoice export** — add a `/invoices/<id>/pdf` route using a
   library like `reportlab` or `weasyprint` to generate a downloadable,
   printable invoice. Good talking point on report generation.
2. **Partial payments** — instead of a single Paid/Pending/Overdue
   status, add a `payments` table (`invoice_id`, `amount`, `paid_on`)
   so an invoice can be partially paid, and compute `amount_due` as
   `total - SUM(payments.amount)`. This is a realistic feature for a
   payments company and adds a 4th related table to talk through.
3. **Basic auth / multi-user support** — add a `users` table and a
   login page (Flask sessions + `werkzeug.security` for password
   hashing), and scope customers/invoices to the logged-in user. Good
   way to demonstrate you understand authentication basics without
   needing a heavy framework.
4. **CSV export** — add a "Download CSV" button on the invoices page
   that hits a `/api/invoices/export` endpoint and streams a CSV using
   Python's built-in `csv` module — an easy add that shows up well on
   a resume bullet ("data export").

## Design decisions worth mentioning in an interview

- **Foreign keys are enforced explicitly.** SQLite has FK constraints
  *off* by default for backward compatibility — this app runs
  `PRAGMA foreign_keys = ON` on every connection so referential
  integrity is actually enforced, not just declared.
- **Totals are recalculated server-side, never trusted from the
  client.** The frontend calculates a live preview for user feedback,
  but the backend always recomputes subtotal/tax/total itself from the
  submitted line items — this matters a lot in a billing/payments
  context where the client should never be able to dictate a monetary
  amount.
- **Overdue detection is lazy, not scheduled.** Rather than a cron job,
  any `Pending` invoice whose due date has passed is flipped to
  `Overdue` at the top of any read (`mark_overdue_invoices()` in
  `app.py`). No extra infrastructure, always correct when viewed.
