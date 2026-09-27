# BillDesk — Intelligent Billing & Business Operations Platform

A full-stack, multi-tenant billing and invoicing platform with real double-entry
bookkeeping, role-based access control, inventory tracking, and a safe
AI query assistant — built to demonstrate production-grade backend engineering,
not just CRUD.

## Live demo

https://billing-invoice-system-hdmf.onrender.com

*(Free-tier hosting — first load after inactivity can take 30–60 seconds to wake up.)*

## Tech stack

| Layer     | Technology |
|-----------|------------|
| Backend   | Python 3 + Flask (REST API, versioned under `/api/v1/`) |
| Database  | SQLite (via Python's built-in `sqlite3`) |
| Auth      | Session cookies (web) + JWT (API clients), Werkzeug password hashing |
| Frontend  | Plain HTML, CSS, JavaScript (`fetch` API) — no framework |
| Testing   | pytest (23 tests covering business logic, RBAC, and financial correctness) |
| Container | Docker + docker-compose (optional, not required to run) |
| CI        | GitHub Actions — runs the full test suite on every push |

## Core features

### Multi-tenant organizations
Every account belongs to an **organization**. Registering publicly always
creates a brand-new company account (you become its Admin); teammates are
added via an Admin-only invite, sharing the same customers, invoices, and
products. Organizations are fully isolated from one another — verified by
dedicated tests, including a caught-and-fixed bug where the audit log
originally leaked across tenants.

### Role-based access control (RBAC)
Three roles — **Admin**, **Accountant**, **Sales Staff** — enforced
server-side on every sensitive action (not just hidden buttons in the UI).
Example: only Admin/Accountant can record a payment; only Admin can delete
a customer or invoice.

### Double-entry ledger
Every invoice and payment writes matched **debit and credit** entries to an
append-only ledger (`accounts_receivable`, `cash`, `revenue` accounts) —
the same principle real accounting/payments systems use to guarantee money
is never lost or duplicated. A `/ledger/verify` endpoint mathematically
proves total debits equal total credits at any time. Invoices with ledger
history cannot be hard-deleted — only Cancelled — because real financial
systems never erase money history.

### Idempotency keys on payments
A payment request can include an `idempotency_key`. If the same key is
sent twice (e.g. a retried network call), the second request is recognized
and returns the original result instead of double-charging — the same
mechanism Stripe's API uses.

### Inventory & stock tracking
Products have tracked stock. Invoicing a product automatically deducts
stock (and refuses to oversell); every stock change — sale, manual
adjustment, or reversal — is logged in an append-only movement history.
Low-stock crossing triggers a notification to Admin/Accountant users.

### Invoice numbering, lifecycle & versioning
Human-readable invoice numbers (`INV-2026-001`, resetting yearly).
Status lifecycle: Draft → Pending/Paid/Overdue/Cancelled. Every edit to an
issued invoice snapshots its prior state (who changed it, old total, old
status) before applying the change — a full edit history, not silent
overwrites.

### Recurring invoices
Rules generate a new invoice automatically once their scheduled date
arrives (Monthly/Quarterly/Yearly). No background worker required — a
"Run Due Invoices Now" trigger checks and generates anything due; in
production this would be called by a scheduled job.

### AI business assistant
Ask questions like *"Which customers have overdue payments?"* in plain
English. Deliberately **not** a free-text-to-SQL system — an LLM asked to
generate arbitrary SQL against a production database is a serious
injection/data-exfiltration risk. Instead, questions are matched to a
small set of safe, predefined, parameterized queries — the model only
ever picks *which* approved query to run, never *what* SQL to run.

### Risk scoring
A rule-based heuristic (overdue ratio, invoice size, payment history) scores
each customer Low/Medium/High risk with stated reasons — structured the
same way a trained ML model's output would be, so it's a drop-in
replacement point if real historical data becomes available later.

### Audit logging
Every sensitive action (delete, role change, payment) is recorded with
who, what, when, and details — scoped per-organization, Admin-only.

### Notifications
In-app alerts for invoice creation, payments received, invoices going
overdue, and low stock — with a read/unread bell in the navbar.

### Other
Pagination on invoice listings · CSV export · PDF invoice generation ·
customer lifetime stats (billed/paid/outstanding) · full test suite ·
API versioning under `/api/v1/`.

## Database schema (13 tables)

organizations ─┬─ users (role: Admin/Accountant/Sales Staff)
├─ customers ─┬─ invoices ─┬─ invoice_items ── products
│ │ ├─ payments (idempotency_key)
│ │ └─ invoice_versions
│ └─ recurring_rules
├─ products ── stock_movements
├─ ledger_entries (debit/credit, per invoice)
├─ audit_log
└─ notifications


Every table that holds business data carries `org_id` — the tenant
boundary, checked on every query, not just at login.

## How to run locally

```cmd
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python app.py
```
Open `http://127.0.0.1:5000`, register an account (becomes Admin of a new
organization automatically).

**To populate realistic demo data** (customers with varying risk levels, a
low-stock product, invoices in every status, a balanced ledger, a due
recurring rule):
```cmd
python seed.py
```

## Running tests

```cmd
pip install pytest
pytest tests.py -v
```
23 tests covering: auth & RBAC, cross-tenant isolation, invoice total
calculation (and that the server never trusts client-sent totals),
overdue detection, partial/full payments, inventory (oversell protection,
stock reversal on delete), the double-entry ledger (balance verification,
idempotent payment retries, reversal on payment deletion), and hard-delete
protection on invoices with financial history.

## API overview (`/api/v1/`)

| Area | Endpoints |
|---|---|
| Auth | `POST /auth/login` (JWT), `/register`, `/login`, `/logout` (session) |
| Organizations | `POST /organizations/invite`, `GET /organizations/users` |
| Customers | full CRUD, `GET /customers/<id>/stats`, `GET /customers/<id>/risk-score` |
| Products | full CRUD, `POST /products/<id>/adjust-stock`, `GET /products/<id>/stock-history` |
| Invoices | full CRUD (paginated list), `PATCH /status`, `GET /versions` |
| Payments | `POST /invoices/<id>/payments` (idempotent), `DELETE /payments/<id>` |
| Ledger | `GET /ledger`, `GET /ledger/balances`, `GET /ledger/verify` |
| Recurring | full CRUD, `POST /recurring/run` |
| Reports | `GET /dashboard`, `GET /dashboard/revenue-by-month` |
| Audit / Notifications | `GET /audit-log`, `GET /notifications`, `PATCH /notifications/<id>/read` |
| Assistant | `POST /assistant/ask` |

## Deliberately not built

- **Redis / background job queue** — recurring invoices and notifications
  use a manual/synchronous trigger instead, to avoid requiring
  infrastructure most free hosting doesn't support.
- **Real ML model** — risk scoring is a documented rule-based heuristic; no
  historical dataset exists to train a real model on.
- **Docker as the deployment path** — included for completeness and local
  use, but the live deployment runs directly via Render + gunicorn.

## Design decisions worth discussing

- **Foreign keys are explicitly enforced** (`PRAGMA foreign_keys = ON`) —
  SQLite disables this by default for backward compatibility.
- **Money totals are always recalculated server-side**, never trusted from
  the client, even though the frontend shows a live preview.
- **Ledger entries are append-only** — corrections are always offsetting
  entries, never edits or deletes, preserving full financial history.
- **Role is re-verified from the database on every request**, not cached
  in the session/JWT — so a permission change takes effect immediately,
  not on next login.