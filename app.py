"""
Billing & Invoice Management System
------------------------------------
Flask + SQLite backend. Serves HTML pages (server-rendered shells) and a
small JSON REST API that the frontend JS calls with fetch().

Every customer belongs to a logged-in user (users.id -> customers.user_id).
Invoices and invoice_items don't store user_id directly - ownership is
always checked by joining through customers, so there's exactly one place
("does this customer belong to me?") that decides access.
"""

import csv
import io
import os
import sqlite3
from datetime import date
from functools import wraps

from flask import (
    Flask, g, jsonify, redirect, render_template, request, session,
    send_file, url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# DB_PATH can be overridden via env var so a Railway volume (persistent disk)
# can be mounted somewhere other than the app's own code directory.
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "billing.db"))
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")

app = Flask(__name__)
# SECRET_KEY signs the session cookie. In production this MUST come from an
# environment variable - if it's hardcoded and someone reads the source code,
# they can forge login sessions. The fallback here is only for local dev.
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-secret-change-me")
# ---------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------
def login_required(view):
    """Redirect to /login (or 401 for API calls) if nobody is logged in."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Not authenticated"}), 401
            return redirect(url_for("login_page"))
        return view(*args, **kwargs)
    return wrapped


def current_user_id():
    return session["user_id"]


@app.context_processor
def inject_user():
    """Makes {{ username }} / {{ is_logged_in }} available in every template."""
    return {
        "is_logged_in": "user_id" in session,
        "username": session.get("username"),
    }


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
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Run schema.sql once at startup. Safe to call every time (uses IF NOT EXISTS)."""
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.close()


# Run once at import time (not just under `python app.py`) - this is what
# actually creates the tables when gunicorn imports this module in
# production, since gunicorn never executes the `if __name__ == "__main__"`
# block below.
init_db()


def mark_overdue_invoices(db, user_id):
    """Any of this user's 'Pending' invoices past their due date become 'Overdue'."""
    today = date.today().isoformat()
    db.execute(
        """UPDATE invoices SET status = 'Overdue'
           WHERE status = 'Pending' AND due_date < ?
             AND customer_id IN (SELECT id FROM customers WHERE user_id = ?)""",
        (today, user_id),
    )
    db.commit()


def recalculate_totals(items, tax_percent):
    """Single source of truth for the subtotal/tax/total math used on create + update."""
    subtotal = sum(float(i["quantity"]) * float(i["price"]) for i in items)
    tax_amount = subtotal * float(tax_percent) / 100
    total = subtotal + tax_amount
    return round(subtotal, 2), round(tax_amount, 2), round(total, 2)


def get_owned_invoice(db, invoice_id, user_id):
    """
    Fetch an invoice row, but ONLY if it belongs (via its customer) to this
    user. This is the ownership check used by every invoice-specific route -
    without it, one logged-in user could read/edit/delete another user's
    invoice just by guessing an id in the URL.
    """
    return db.execute(
        """SELECT invoices.*, customers.name AS customer_name
           FROM invoices JOIN customers ON invoices.customer_id = customers.id
           WHERE invoices.id = ? AND customers.user_id = ?""",
        (invoice_id, user_id),
    ).fetchone()


def get_amount_paid(db, invoice_id):
    row = db.execute(
        "SELECT COALESCE(SUM(amount), 0) AS paid FROM payments WHERE invoice_id = ?",
        (invoice_id,),
    ).fetchone()
    return round(row["paid"], 2)


# ---------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------
@app.route("/register", methods=["GET", "POST"])
def register_page():
    if request.method == "GET":
        return render_template("register.html", error=None)

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    if not username or not password:
        return render_template("register.html", error="Username and password are required.")
    if len(password) < 6:
        return render_template("register.html", error="Password must be at least 6 characters.")

    db = get_db()
    existing = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if existing:
        return render_template("register.html", error="That username is already taken.")

    password_hash = generate_password_hash(password)
    cur = db.execute(
        "INSERT INTO users (username, password_hash) VALUES (?, ?)",
        (username, password_hash),
    )
    db.commit()

    session["user_id"] = cur.lastrowid
    session["username"] = username
    return redirect(url_for("dashboard_page"))


@app.route("/login", methods=["GET", "POST"])
def login_page():
    if request.method == "GET":
        return render_template("login.html", error=None)

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

    # check_password_hash handles the hashing itself - we never compare
    # plaintext passwords, and never store one either.
    if user is None or not check_password_hash(user["password_hash"], password):
        return render_template("login.html", error="Invalid username or password.")

    session["user_id"] = user["id"]
    session["username"] = user["username"]
    return redirect(url_for("dashboard_page"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login_page"))


# ---------------------------------------------------------------
# Page routes (render the HTML shell; JS fills in data via the API)
# ---------------------------------------------------------------
@app.route("/")
@login_required
def dashboard_page():
    return render_template("dashboard.html")


@app.route("/customers")
@login_required
def customers_page():
    return render_template("customers.html")


@app.route("/invoices")
@login_required
def invoices_page():
    return render_template("invoices.html")


@app.route("/invoices/new")
@login_required
def new_invoice_page():
    return render_template("invoice_form.html", invoice_id=None)


@app.route("/invoices/<int:invoice_id>/edit")
@login_required
def edit_invoice_page(invoice_id):
    return render_template("invoice_form.html", invoice_id=invoice_id)


# ---------------------------------------------------------------
# API: customers
# ---------------------------------------------------------------
@app.route("/api/customers", methods=["GET"])
@login_required
def get_customers():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM customers WHERE user_id = ? ORDER BY name COLLATE NOCASE",
        (current_user_id(),),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/customers/<int:customer_id>", methods=["GET"])
@login_required
def get_customer(customer_id):
    db = get_db()
    row = db.execute(
        "SELECT * FROM customers WHERE id = ? AND user_id = ?",
        (customer_id, current_user_id()),
    ).fetchone()
    if row is None:
        return jsonify({"error": "Customer not found"}), 404
    return jsonify(dict(row))


@app.route("/api/customers", methods=["POST"])
@login_required
def add_customer():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400

    db = get_db()
    cur = db.execute(
        "INSERT INTO customers (user_id, name, email, phone, address) VALUES (?, ?, ?, ?, ?)",
        (current_user_id(), name, data.get("email", "").strip(),
         data.get("phone", "").strip(), data.get("address", "").strip()),
    )
    db.commit()
    return jsonify({"id": cur.lastrowid}), 201


@app.route("/api/customers/<int:customer_id>", methods=["PUT"])
@login_required
def update_customer(customer_id):
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400

    db = get_db()
    cur = db.execute(
        "UPDATE customers SET name = ?, email = ?, phone = ?, address = ? WHERE id = ? AND user_id = ?",
        (name, data.get("email", ""), data.get("phone", ""), data.get("address", ""),
         customer_id, current_user_id()),
    )
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "Customer not found"}), 404
    return jsonify({"success": True})


@app.route("/api/customers/<int:customer_id>", methods=["DELETE"])
@login_required
def delete_customer(customer_id):
    db = get_db()
    try:
        cur = db.execute(
            "DELETE FROM customers WHERE id = ? AND user_id = ?",
            (customer_id, current_user_id()),
        )
        db.commit()
    except sqlite3.IntegrityError:
        # Fires because invoices.customer_id has a FK pointing at this row
        # and we deliberately did NOT set ON DELETE CASCADE.
        return jsonify({"error": "Cannot delete a customer that has invoices. Delete their invoices first."}), 400
    if cur.rowcount == 0:
        return jsonify({"error": "Customer not found"}), 404
    return jsonify({"success": True})


# ---------------------------------------------------------------
# API: invoices
# ---------------------------------------------------------------
def build_invoice_query(user_id, search, status):
    query = """
        SELECT invoices.*, customers.name AS customer_name
        FROM invoices
        JOIN customers ON invoices.customer_id = customers.id
        WHERE customers.user_id = ?
    """
    params = [user_id]
    if search:
        query += " AND customers.name LIKE ?"
        params.append(f"%{search}%")
    if status:
        query += " AND invoices.status = ?"
        params.append(status)
    return query, params


@app.route("/api/invoices", methods=["GET"])
@login_required
def get_invoices():
    """Supports optional ?search=<customer name>&status=<Paid|Pending|Overdue>"""
    db = get_db()
    mark_overdue_invoices(db, current_user_id())

    search = request.args.get("search", "").strip()
    status = request.args.get("status", "").strip()
    query, params = build_invoice_query(current_user_id(), search, status)
    query += " ORDER BY invoices.invoice_date DESC, invoices.id DESC"

    rows = db.execute(query, params).fetchall()
    invoices = []
    for r in rows:
        inv = dict(r)
        paid = get_amount_paid(db, inv["id"])
        inv["amount_paid"] = paid
        inv["amount_due"] = round(inv["total"] - paid, 2)
        invoices.append(inv)
    return jsonify(invoices)


@app.route("/api/invoices/<int:invoice_id>", methods=["GET"])
@login_required
def get_invoice(invoice_id):
    db = get_db()
    invoice = get_owned_invoice(db, invoice_id, current_user_id())
    if invoice is None:
        return jsonify({"error": "Invoice not found"}), 404

    items = db.execute(
        "SELECT * FROM invoice_items WHERE invoice_id = ?", (invoice_id,)
    ).fetchall()
    payments = db.execute(
        "SELECT * FROM payments WHERE invoice_id = ? ORDER BY paid_on DESC, id DESC", (invoice_id,)
    ).fetchall()

    result = dict(invoice)
    result["items"] = [dict(i) for i in items]
    result["payments"] = [dict(p) for p in payments]
    result["amount_paid"] = get_amount_paid(db, invoice_id)
    result["amount_due"] = round(result["total"] - result["amount_paid"], 2)
    return jsonify(result)


@app.route("/api/invoices", methods=["POST"])
@login_required
def create_invoice():
    data = request.get_json(force=True)
    customer_id = data.get("customer_id")
    items = data.get("items", [])

    if not customer_id:
        return jsonify({"error": "Customer is required"}), 400
    if not items:
        return jsonify({"error": "At least one line item is required"}), 400

    db = get_db()
    # Make sure the chosen customer actually belongs to this user -
    # otherwise a user could bill an invoice against someone else's customer.
    owned = db.execute(
        "SELECT id FROM customers WHERE id = ? AND user_id = ?", (customer_id, current_user_id())
    ).fetchone()
    if owned is None:
        return jsonify({"error": "Invalid customer"}), 400

    tax_percent = data.get("tax_percent", 0)
    subtotal, tax_amount, total = recalculate_totals(items, tax_percent)

    cur = db.execute(
        """INSERT INTO invoices
           (customer_id, invoice_date, due_date, tax_percent, subtotal, tax_amount, total, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            customer_id, data.get("invoice_date"), data.get("due_date"),
            tax_percent, subtotal, tax_amount, total, data.get("status", "Pending"),
        ),
    )
    invoice_id = cur.lastrowid

    for item in items:
        db.execute(
            "INSERT INTO invoice_items (invoice_id, item_name, quantity, price) VALUES (?, ?, ?, ?)",
            (invoice_id, item["item_name"], item["quantity"], item["price"]),
        )
    db.commit()
    return jsonify({"id": invoice_id}), 201


@app.route("/api/invoices/<int:invoice_id>", methods=["PUT"])
@login_required
def update_invoice(invoice_id):
    db = get_db()
    if get_owned_invoice(db, invoice_id, current_user_id()) is None:
        return jsonify({"error": "Invoice not found"}), 404

    data = request.get_json(force=True)
    items = data.get("items", [])
    if not items:
        return jsonify({"error": "At least one line item is required"}), 400

    tax_percent = data.get("tax_percent", 0)
    subtotal, tax_amount, total = recalculate_totals(items, tax_percent)

    db.execute(
        """UPDATE invoices
           SET customer_id = ?, invoice_date = ?, due_date = ?, tax_percent = ?,
               subtotal = ?, tax_amount = ?, total = ?, status = ?
           WHERE id = ?""",
        (
            data.get("customer_id"), data.get("invoice_date"), data.get("due_date"),
            tax_percent, subtotal, tax_amount, total, data.get("status", "Pending"), invoice_id,
        ),
    )
    # Simplest correct way to sync line items: wipe and re-insert.
    db.execute("DELETE FROM invoice_items WHERE invoice_id = ?", (invoice_id,))
    for item in items:
        db.execute(
            "INSERT INTO invoice_items (invoice_id, item_name, quantity, price) VALUES (?, ?, ?, ?)",
            (invoice_id, item["item_name"], item["quantity"], item["price"]),
        )
    db.commit()
    return jsonify({"success": True})


@app.route("/api/invoices/<int:invoice_id>/status", methods=["PATCH"])
@login_required
def update_invoice_status(invoice_id):
    db = get_db()
    if get_owned_invoice(db, invoice_id, current_user_id()) is None:
        return jsonify({"error": "Invoice not found"}), 404

    data = request.get_json(force=True)
    status = data.get("status")
    if status not in ("Paid", "Pending", "Overdue"):
        return jsonify({"error": "Invalid status"}), 400
    db.execute("UPDATE invoices SET status = ? WHERE id = ?", (status, invoice_id))
    db.commit()
    return jsonify({"success": True})


@app.route("/api/invoices/<int:invoice_id>", methods=["DELETE"])
@login_required
def delete_invoice(invoice_id):
    db = get_db()
    if get_owned_invoice(db, invoice_id, current_user_id()) is None:
        return jsonify({"error": "Invoice not found"}), 404
    db.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))  # cascades to items + payments
    db.commit()
    return jsonify({"success": True})


# ---------------------------------------------------------------
# API: payments (partial payments against an invoice)
# ---------------------------------------------------------------
@app.route("/api/invoices/<int:invoice_id>/payments", methods=["POST"])
@login_required
def add_payment(invoice_id):
    db = get_db()
    invoice = get_owned_invoice(db, invoice_id, current_user_id())
    if invoice is None:
        return jsonify({"error": "Invoice not found"}), 404

    data = request.get_json(force=True)
    try:
        amount = round(float(data.get("amount")), 2)
    except (TypeError, ValueError):
        return jsonify({"error": "A valid payment amount is required"}), 400
    if amount <= 0:
        return jsonify({"error": "Payment amount must be greater than zero"}), 400

    paid_on = data.get("paid_on") or date.today().isoformat()
    note = (data.get("note") or "").strip()

    db.execute(
        "INSERT INTO payments (invoice_id, amount, paid_on, note) VALUES (?, ?, ?, ?)",
        (invoice_id, amount, paid_on, note),
    )

    # If this payment (plus any earlier ones) covers the full total,
    # auto-flip the invoice to Paid. Partial payments don't change status -
    # the UI shows "amount_due" so the user can see it's partially settled
    # without needing a 4th status value that would break the CHECK constraint.
    total_paid = get_amount_paid(db, invoice_id)
    if total_paid >= invoice["total"]:
        db.execute("UPDATE invoices SET status = 'Paid' WHERE id = ?", (invoice_id,))

    db.commit()
    return jsonify({"success": True, "amount_paid": total_paid, "amount_due": round(invoice["total"] - total_paid, 2)}), 201


@app.route("/api/payments/<int:payment_id>", methods=["DELETE"])
@login_required
def delete_payment(payment_id):
    db = get_db()
    # Ownership check: walk payment -> invoice -> customer -> user
    row = db.execute(
        """SELECT payments.id, payments.invoice_id FROM payments
           JOIN invoices ON payments.invoice_id = invoices.id
           JOIN customers ON invoices.customer_id = customers.id
           WHERE payments.id = ? AND customers.user_id = ?""",
        (payment_id, current_user_id()),
    ).fetchone()
    if row is None:
        return jsonify({"error": "Payment not found"}), 404

    db.execute("DELETE FROM payments WHERE id = ?", (payment_id,))
    # Removing a payment can un-pay an invoice that was auto-marked Paid.
    invoice = db.execute("SELECT * FROM invoices WHERE id = ?", (row["invoice_id"],)).fetchone()
    total_paid = get_amount_paid(db, row["invoice_id"])
    if invoice["status"] == "Paid" and total_paid < invoice["total"]:
        today = date.today().isoformat()
        new_status = "Overdue" if invoice["due_date"] < today else "Pending"
        db.execute("UPDATE invoices SET status = ? WHERE id = ?", (new_status, row["invoice_id"]))
    db.commit()
    return jsonify({"success": True})


# ---------------------------------------------------------------
# API: dashboard
# ---------------------------------------------------------------
@app.route("/api/dashboard", methods=["GET"])
@login_required
def dashboard_data():
    db = get_db()
    user_id = current_user_id()
    mark_overdue_invoices(db, user_id)

    def scalar(sql, params):
        return db.execute(sql, params).fetchone()[0]

    base = "FROM invoices JOIN customers ON invoices.customer_id = customers.id WHERE customers.user_id = ?"

    total_revenue = scalar(f"SELECT COALESCE(SUM(total), 0) {base} AND invoices.status = 'Paid'", (user_id,))
    total_pending = scalar(f"SELECT COALESCE(SUM(total), 0) {base} AND invoices.status = 'Pending'", (user_id,))
    overdue_amount = scalar(f"SELECT COALESCE(SUM(total), 0) {base} AND invoices.status = 'Overdue'", (user_id,))
    overdue_count = scalar(f"SELECT COUNT(*) {base} AND invoices.status = 'Overdue'", (user_id,))
    total_invoices = scalar(f"SELECT COUNT(*) {base}", (user_id,))

    return jsonify({
        "total_revenue": total_revenue,
        "total_pending": total_pending,
        "overdue_amount": overdue_amount,
        "overdue_count": overdue_count,
        "total_invoices": total_invoices,
    })


# ---------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------
@app.route("/api/invoices/export", methods=["GET"])
@login_required
def export_invoices_csv():
    db = get_db()
    mark_overdue_invoices(db, current_user_id())

    search = request.args.get("search", "").strip()
    status = request.args.get("status", "").strip()
    query, params = build_invoice_query(current_user_id(), search, status)
    query += " ORDER BY invoices.invoice_date DESC"
    rows = db.execute(query, params).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Invoice ID", "Customer", "Invoice Date", "Due Date",
                      "Subtotal", "Tax %", "Tax Amount", "Total", "Amount Paid", "Amount Due", "Status"])
    for r in rows:
        paid = get_amount_paid(db, r["id"])
        writer.writerow([
            r["id"], r["customer_name"], r["invoice_date"], r["due_date"],
            r["subtotal"], r["tax_percent"], r["tax_amount"], r["total"],
            paid, round(r["total"] - paid, 2), r["status"],
        ])

    mem = io.BytesIO(output.getvalue().encode("utf-8"))
    return send_file(
        mem, mimetype="text/csv", as_attachment=True,
        download_name="invoices_export.csv",
    )


# ---------------------------------------------------------------
# PDF export (single invoice)
# ---------------------------------------------------------------
@app.route("/invoices/<int:invoice_id>/pdf", methods=["GET"])
@login_required
def export_invoice_pdf(invoice_id):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet

    db = get_db()
    invoice = get_owned_invoice(db, invoice_id, current_user_id())
    if invoice is None:
        return jsonify({"error": "Invoice not found"}), 404

    items = db.execute("SELECT * FROM invoice_items WHERE invoice_id = ?", (invoice_id,)).fetchall()
    amount_paid = get_amount_paid(db, invoice_id)
    amount_due = round(invoice["total"] - amount_paid, 2)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=30 * mm)
    styles = getSampleStyleSheet()
    story = [
        Paragraph(f"Invoice #{invoice['id']}", styles["Title"]),
        Paragraph(f"Bill to: {invoice['customer_name']}", styles["Normal"]),
        Paragraph(f"Invoice date: {invoice['invoice_date']}   |   Due date: {invoice['due_date']}", styles["Normal"]),
        Paragraph(f"Status: {invoice['status']}", styles["Normal"]),
        Spacer(1, 12),
    ]

    table_data = [["Item", "Qty", "Price", "Line Total"]]
    for item in items:
        line_total = item["quantity"] * item["price"]
        table_data.append([item["item_name"], str(item["quantity"]), f"{item['price']:.2f}", f"{line_total:.2f}"])
    table_data.append(["", "", "Subtotal", f"{invoice['subtotal']:.2f}"])
    table_data.append(["", "", f"Tax ({invoice['tax_percent']}%)", f"{invoice['tax_amount']:.2f}"])
    table_data.append(["", "", "Total", f"{invoice['total']:.2f}"])
    table_data.append(["", "", "Amount Paid", f"{amount_paid:.2f}"])
    table_data.append(["", "", "Amount Due", f"{amount_due:.2f}"])

    table = Table(table_data, colWidths=[70 * mm, 25 * mm, 35 * mm, 35 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2563eb")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTNAME", (2, len(table_data) - 4), (-1, -1), "Helvetica-Bold"),
    ]))
    story.append(table)
    doc.build(story)
    buffer.seek(0)

    return send_file(
        buffer, mimetype="application/pdf", as_attachment=True,
        download_name=f"invoice_{invoice_id}.pdf",
    )


if __name__ == "__main__":
    app.run(debug=True)