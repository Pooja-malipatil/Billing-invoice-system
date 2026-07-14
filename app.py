"""
Billing & Invoice Management System
------------------------------------
Flask + SQLite backend. Serves HTML pages (server-rendered shells) and a
small JSON REST API that the frontend JS calls with fetch().
"""

import os
import sqlite3
from datetime import date

from flask import Flask, g, jsonify, render_template, request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "billing.db")
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")

app = Flask(__name__)


# ---------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------
def get_db():
    """Return a request-scoped SQLite connection (created once per request)."""
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row  # lets us access columns by name, e.g. row["name"]
        g.db.execute("PRAGMA foreign_keys = ON")  # SQLite disables FK checks by default!
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    """Flask calls this automatically after every request to clean up."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Run schema.sql once at startup. Safe to call every time (uses IF NOT EXISTS)."""
    conn = sqlite3.connect(DB_PATH)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.close()


def mark_overdue_invoices(db):
    """
    Any invoice still 'Pending' whose due_date has passed becomes 'Overdue'.
    Called at the top of read endpoints so status is always accurate without
    needing a background job/cron.
    """
    today = date.today().isoformat()
    db.execute(
        "UPDATE invoices SET status = 'Overdue' WHERE status = 'Pending' AND due_date < ?",
        (today,),
    )
    db.commit()


def recalculate_totals(items, tax_percent):
    """Single source of truth for the subtotal/tax/total math used on create + update."""
    subtotal = sum(float(i["quantity"]) * float(i["price"]) for i in items)
    tax_amount = subtotal * float(tax_percent) / 100
    total = subtotal + tax_amount
    return round(subtotal, 2), round(tax_amount, 2), round(total, 2)


# ---------------------------------------------------------------
# Page routes (render the HTML shell; JS fills in data via the API)
# ---------------------------------------------------------------
@app.route("/")
def dashboard_page():
    return render_template("dashboard.html")


@app.route("/customers")
def customers_page():
    return render_template("customers.html")


@app.route("/invoices")
def invoices_page():
    return render_template("invoices.html")


@app.route("/invoices/new")
def new_invoice_page():
    return render_template("invoice_form.html", invoice_id=None)


@app.route("/invoices/<int:invoice_id>/edit")
def edit_invoice_page(invoice_id):
    return render_template("invoice_form.html", invoice_id=invoice_id)


# ---------------------------------------------------------------
# API: customers
# ---------------------------------------------------------------
@app.route("/api/customers", methods=["GET"])
def get_customers():
    db = get_db()
    rows = db.execute("SELECT * FROM customers ORDER BY name COLLATE NOCASE").fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/customers/<int:customer_id>", methods=["GET"])
def get_customer(customer_id):
    db = get_db()
    row = db.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    if row is None:
        return jsonify({"error": "Customer not found"}), 404
    return jsonify(dict(row))


@app.route("/api/customers", methods=["POST"])
def add_customer():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400

    db = get_db()
    cur = db.execute(
        "INSERT INTO customers (name, email, phone, address) VALUES (?, ?, ?, ?)",
        (name, data.get("email", "").strip(), data.get("phone", "").strip(), data.get("address", "").strip()),
    )
    db.commit()
    return jsonify({"id": cur.lastrowid}), 201


@app.route("/api/customers/<int:customer_id>", methods=["PUT"])
def update_customer(customer_id):
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400

    db = get_db()
    db.execute(
        "UPDATE customers SET name = ?, email = ?, phone = ?, address = ? WHERE id = ?",
        (name, data.get("email", ""), data.get("phone", ""), data.get("address", ""), customer_id),
    )
    db.commit()
    return jsonify({"success": True})


@app.route("/api/customers/<int:customer_id>", methods=["DELETE"])
def delete_customer(customer_id):
    db = get_db()
    try:
        db.execute("DELETE FROM customers WHERE id = ?", (customer_id,))
        db.commit()
    except sqlite3.IntegrityError:
        # Fires because invoices.customer_id has a FK pointing at this row
        # and we deliberately did NOT set ON DELETE CASCADE.
        return jsonify({"error": "Cannot delete a customer that has invoices. Delete their invoices first."}), 400
    return jsonify({"success": True})


# ---------------------------------------------------------------
# API: invoices
# ---------------------------------------------------------------
@app.route("/api/invoices", methods=["GET"])
def get_invoices():
    """Supports optional ?search=<customer name>&status=<Paid|Pending|Overdue>"""
    db = get_db()
    mark_overdue_invoices(db)

    search = request.args.get("search", "").strip()
    status = request.args.get("status", "").strip()

    query = """
        SELECT invoices.*, customers.name AS customer_name
        FROM invoices
        JOIN customers ON invoices.customer_id = customers.id
        WHERE 1 = 1
    """
    params = []
    if search:
        query += " AND customers.name LIKE ?"
        params.append(f"%{search}%")
    if status:
        query += " AND invoices.status = ?"
        params.append(status)
    query += " ORDER BY invoices.invoice_date DESC, invoices.id DESC"

    rows = db.execute(query, params).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/invoices/<int:invoice_id>", methods=["GET"])
def get_invoice(invoice_id):
    db = get_db()
    invoice = db.execute(
        """SELECT invoices.*, customers.name AS customer_name
           FROM invoices JOIN customers ON invoices.customer_id = customers.id
           WHERE invoices.id = ?""",
        (invoice_id,),
    ).fetchone()
    if invoice is None:
        return jsonify({"error": "Invoice not found"}), 404

    items = db.execute(
        "SELECT * FROM invoice_items WHERE invoice_id = ?", (invoice_id,)
    ).fetchall()

    result = dict(invoice)
    result["items"] = [dict(i) for i in items]
    return jsonify(result)


@app.route("/api/invoices", methods=["POST"])
def create_invoice():
    data = request.get_json(force=True)
    customer_id = data.get("customer_id")
    items = data.get("items", [])

    if not customer_id:
        return jsonify({"error": "Customer is required"}), 400
    if not items:
        return jsonify({"error": "At least one line item is required"}), 400

    tax_percent = data.get("tax_percent", 0)
    subtotal, tax_amount, total = recalculate_totals(items, tax_percent)

    db = get_db()
    try:
        cur = db.execute(
            """INSERT INTO invoices
               (customer_id, invoice_date, due_date, tax_percent, subtotal, tax_amount, total, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                customer_id,
                data.get("invoice_date"),
                data.get("due_date"),
                tax_percent,
                subtotal,
                tax_amount,
                total,
                data.get("status", "Pending"),
            ),
        )
        invoice_id = cur.lastrowid

        for item in items:
            db.execute(
                "INSERT INTO invoice_items (invoice_id, item_name, quantity, price) VALUES (?, ?, ?, ?)",
                (invoice_id, item["item_name"], item["quantity"], item["price"]),
            )
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return jsonify({"error": "Invalid customer_id"}), 400

    return jsonify({"id": invoice_id}), 201


@app.route("/api/invoices/<int:invoice_id>", methods=["PUT"])
def update_invoice(invoice_id):
    data = request.get_json(force=True)
    items = data.get("items", [])
    if not items:
        return jsonify({"error": "At least one line item is required"}), 400

    tax_percent = data.get("tax_percent", 0)
    subtotal, tax_amount, total = recalculate_totals(items, tax_percent)

    db = get_db()
    db.execute(
        """UPDATE invoices
           SET customer_id = ?, invoice_date = ?, due_date = ?, tax_percent = ?,
               subtotal = ?, tax_amount = ?, total = ?, status = ?
           WHERE id = ?""",
        (
            data.get("customer_id"),
            data.get("invoice_date"),
            data.get("due_date"),
            tax_percent,
            subtotal,
            tax_amount,
            total,
            data.get("status", "Pending"),
            invoice_id,
        ),
    )
    # Simplest correct way to sync line items: wipe and re-insert.
    # (Fine at this scale; a diff-based update would be overkill here.)
    db.execute("DELETE FROM invoice_items WHERE invoice_id = ?", (invoice_id,))
    for item in items:
        db.execute(
            "INSERT INTO invoice_items (invoice_id, item_name, quantity, price) VALUES (?, ?, ?, ?)",
            (invoice_id, item["item_name"], item["quantity"], item["price"]),
        )
    db.commit()
    return jsonify({"success": True})


@app.route("/api/invoices/<int:invoice_id>/status", methods=["PATCH"])
def update_invoice_status(invoice_id):
    """Small dedicated endpoint just for the 'Mark as Paid' quick action in the UI."""
    data = request.get_json(force=True)
    status = data.get("status")
    if status not in ("Paid", "Pending", "Overdue"):
        return jsonify({"error": "Invalid status"}), 400
    db = get_db()
    db.execute("UPDATE invoices SET status = ? WHERE id = ?", (status, invoice_id))
    db.commit()
    return jsonify({"success": True})


@app.route("/api/invoices/<int:invoice_id>", methods=["DELETE"])
def delete_invoice(invoice_id):
    db = get_db()
    db.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))  # cascades to invoice_items
    db.commit()
    return jsonify({"success": True})


# ---------------------------------------------------------------
# API: dashboard
# ---------------------------------------------------------------
@app.route("/api/dashboard", methods=["GET"])
def dashboard_data():
    db = get_db()
    mark_overdue_invoices(db)

    total_revenue = db.execute(
        "SELECT COALESCE(SUM(total), 0) AS t FROM invoices WHERE status = 'Paid'"
    ).fetchone()["t"]

    total_pending = db.execute(
        "SELECT COALESCE(SUM(total), 0) AS t FROM invoices WHERE status = 'Pending'"
    ).fetchone()["t"]

    overdue_amount = db.execute(
        "SELECT COALESCE(SUM(total), 0) AS t FROM invoices WHERE status = 'Overdue'"
    ).fetchone()["t"]

    overdue_count = db.execute(
        "SELECT COUNT(*) AS c FROM invoices WHERE status = 'Overdue'"
    ).fetchone()["c"]

    total_invoices = db.execute("SELECT COUNT(*) AS c FROM invoices").fetchone()["c"]

    return jsonify(
        {
            "total_revenue": total_revenue,
            "total_pending": total_pending,
            "overdue_amount": overdue_amount,
            "overdue_count": overdue_count,
            "total_invoices": total_invoices,
        }
    )


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
