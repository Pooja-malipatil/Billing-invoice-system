"""
BillDesk - Billing & Invoice Management System
-------------------------------------------------
Phase 1 additions on top of the existing app: JWT authentication (alongside
existing session auth) and Role-Based Access Control (Admin / Accountant /
Sales Staff), enforced server-side.
"""

import csv
import io
import os
import sqlite3
from datetime import date, datetime, timedelta
from functools import wraps

import jwt
from flask import (
    Flask, g, jsonify, redirect, render_template, request, session,
    send_file, url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "billing.db"))
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-secret-change-me")

JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24


# ---------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------
def generate_jwt(user_id, username, role):
    """Signed token carrying identity + role. Expires in 24h."""
    payload = {
        "user_id": user_id,
        "username": username,
        "role": role,
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HOURS),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, app.secret_key, algorithm=JWT_ALGORITHM)


def decode_jwt(token):
    try:
        return jwt.decode(token, app.secret_key, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


# ---------------------------------------------------------------
# Auth decorators
# ---------------------------------------------------------------
def login_required(view):
    """Accepts EITHER a session cookie (HTML pages) OR a JWT Bearer token
    (API clients). Both resolve to g.user_id / g.username.

    IMPORTANT: we deliberately do NOT trust the role stored in the session
    or JWT payload. Roles can change after a token/session was issued (an
    Admin can promote/demote someone), and a stale cached role would mean
    that change doesn't take effect until the user logs out and back in.
    Instead we look up the CURRENT role from the database on every request.
    This costs one extra indexed lookup per request but means permission
    changes apply immediately - which matters a lot for a billing system
    where you might need to revoke someone's Accountant access right now,
    not next time they happen to log in.
    """
    @wraps(view)
    def wrapped(*args, **kwargs):
        user_id = None

        if "user_id" in session:
            user_id = session["user_id"]
        else:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                payload = decode_jwt(auth_header[7:])
                if payload:
                    user_id = payload["user_id"]

        if user_id is None:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Not authenticated"}), 401
            return redirect(url_for("login_page"))

        db = get_db()
        user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if user is None:
            # Account was deleted after the token/session was issued
            if request.path.startswith("/api/"):
                return jsonify({"error": "Not authenticated"}), 401
            return redirect(url_for("login_page"))

        g.user_id = user["id"]
        g.username = user["username"]
        g.role = user["role"]      # always fresh from the DB, never from the cached token
        g.org_id = user["org_id"]  # the tenant boundary - fresh from DB for the same reason as role
        return view(*args, **kwargs)
    return wrapped


def role_required(*allowed_roles):
    """Stack UNDER @login_required. Authorization check, separate from
    authentication - enforced here on the backend, never trusted from the
    frontend alone."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.role not in allowed_roles:
                return jsonify({
                    "error": f"Requires role: {', '.join(allowed_roles)}. Your role: {g.role}"
                }), 403
            return view(*args, **kwargs)
        return wrapped
    return decorator


def current_user_id():
    return g.user_id


def current_org_id():
    return g.org_id


@app.context_processor
def inject_user():
    return {
        "is_logged_in": "user_id" in session,
        "username": session.get("username"),
        "role": session.get("role"),
    }


# ---------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.close()


init_db()  # runs at import time so gunicorn (production) also creates tables


def mark_overdue_invoices(db, org_id):
    today = date.today().isoformat()
    # Find which invoices are ABOUT to flip, before updating, so we know
    # exactly which ones to notify about (not every already-overdue one).
    newly_overdue = db.execute(
        """SELECT invoices.id, invoices.invoice_number, customers.name AS customer_name
           FROM invoices JOIN customers ON invoices.customer_id = customers.id
           WHERE invoices.status = 'Pending' AND invoices.due_date < ?
             AND customers.org_id = ?""",
        (today, org_id),
    ).fetchall()

    db.execute(
        """UPDATE invoices SET status = 'Overdue'
           WHERE status = 'Pending' AND due_date < ?
             AND customer_id IN (SELECT id FROM customers WHERE org_id = ?)""",
        (today, org_id),
    )
    if newly_overdue:
        # Notify every Admin/Accountant in the org, not just whoever
        # happened to trigger this check - overdue invoices are everyone's
        # business, not just the person who happened to load the page.
        recipients = db.execute(
            "SELECT id FROM users WHERE org_id = ? AND role IN ('Admin', 'Accountant')", (org_id,)
        ).fetchall()
        for inv in newly_overdue:
            label = inv["invoice_number"] or f"#{inv['id']}"
            for r in recipients:
                notify(db, r["id"], "overdue", f"Invoice {label} for {inv['customer_name']} is now overdue")
    db.commit()


def recalculate_totals(items, tax_percent):
    subtotal = sum(float(i["quantity"]) * float(i["price"]) for i in items)
    tax_amount = subtotal * float(tax_percent) / 100
    total = subtotal + tax_amount
    return round(subtotal, 2), round(tax_amount, 2), round(total, 2)


def get_owned_invoice(db, invoice_id, org_id):
    return db.execute(
        """SELECT invoices.*, customers.name AS customer_name
           FROM invoices JOIN customers ON invoices.customer_id = customers.id
           WHERE invoices.id = ? AND customers.org_id = ?""",
        (invoice_id, org_id),
    ).fetchone()


def get_amount_paid(db, invoice_id):
    row = db.execute(
        "SELECT COALESCE(SUM(amount), 0) AS paid FROM payments WHERE invoice_id = ?",
        (invoice_id,),
    ).fetchone()
    return round(row["paid"], 2)


def adjust_stock(db, product_id, change_amount, reason):
    """
    The ONE function that changes stock. Never write to products.stock_quantity
    directly anywhere else - always come through here, so every change is
    guaranteed to also get logged in stock_movements. This is the same
    "single source of truth" pattern we used for recalculate_totals().
    """
    db.execute(
        "UPDATE products SET stock_quantity = stock_quantity + ? WHERE id = ?",
        (change_amount, product_id),
    )
    db.execute(
        "INSERT INTO stock_movements (product_id, change_amount, reason) VALUES (?, ?, ?)",
        (product_id, change_amount, reason),
    )

    # Low-stock notification: only fire when stock DROPS (change_amount < 0)
    # and crosses at/below the threshold - not on every read, and not when
    # stock is going UP (a restock shouldn't trigger a "running low" alert).
    if change_amount < 0:
        product = db.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if product and product["stock_quantity"] <= product["low_stock_threshold"]:
            recipients = db.execute(
                "SELECT id FROM users WHERE org_id = ? AND role IN ('Admin', 'Accountant')",
                (product["org_id"],),
            ).fetchall()
            for r in recipients:
                notify(db, r["id"], "low_stock",
                       f"'{product['name']}' is low on stock: {product['stock_quantity']} left "
                       f"(threshold {product['low_stock_threshold']})")


def get_owned_product(db, product_id, org_id):
    return db.execute(
        "SELECT * FROM products WHERE id = ? AND org_id = ?", (product_id, org_id)
    ).fetchone()


def generate_invoice_number(db):
    """INV-2026-001, INV-2026-002... resets numbering each calendar year."""
    year = date.today().year
    prefix = f"INV-{year}-"
    last = db.execute(
        "SELECT invoice_number FROM invoices WHERE invoice_number LIKE ? ORDER BY id DESC LIMIT 1",
        (f"{prefix}%",),
    ).fetchone()
    next_seq = int(last["invoice_number"].split("-")[-1]) + 1 if last else 1
    return f"{prefix}{next_seq:03d}"


def log_audit(db, action, entity, entity_id, details=""):
    """Single place every sensitive action gets recorded. Called AFTER the
    real change, in the same transaction/commit, so the log entry and the
    actual change succeed or fail together - never one without the other."""
    db.execute(
        "INSERT INTO audit_log (org_id, user_id, username, action, entity, entity_id, details) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (g.org_id, g.user_id, g.username, action, entity, entity_id, details),
    )


def notify(db, user_id, notif_type, message):
    db.execute(
        "INSERT INTO notifications (user_id, type, message) VALUES (?, ?, ?)",
        (user_id, notif_type, message),
    )


# ---------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------
@app.route("/register", methods=["GET", "POST"])
def register_page():
    if request.method == "GET":
        return render_template("register.html", error=None)

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    org_name = request.form.get("org_name", "").strip() or f"{username}'s Organization"

    if not username or not password:
        return render_template("register.html", error="Username and password are required.")
    if len(password) < 6:
        return render_template("register.html", error="Password must be at least 6 characters.")

    db = get_db()
    existing = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if existing:
        return render_template("register.html", error="That username is already taken.")

    # Public /register always creates a BRAND NEW organization/company
    # account - this is deliberate. Existing companies add teammates
    # through the Admin-only invite endpoint below (POST
    # /api/v1/organizations/invite), not through public self-registration.
    # This mirrors real B2B SaaS signup: anyone can sign up and start a new
    # company account, but you can't just register your way into someone
    # ELSE's company data.
    org_cur = db.execute("INSERT INTO organizations (name) VALUES (?)", (org_name,))
    org_id = org_cur.lastrowid

    # The person who creates a new organization is always its first Admin -
    # someone has to be able to manage it, and they're the only one there.
    password_hash = generate_password_hash(password)
    cur = db.execute(
        "INSERT INTO users (org_id, username, password_hash, role) VALUES (?, ?, ?, ?)",
        (org_id, username, password_hash, "Admin"),
    )
    db.commit()

    session["user_id"] = cur.lastrowid
    session["username"] = username
    session["role"] = "Admin"
    return redirect(url_for("dashboard_page"))


@app.route("/login", methods=["GET", "POST"])
def login_page():
    if request.method == "GET":
        return render_template("login.html", error=None)

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

    if user is None or not check_password_hash(user["password_hash"], password):
        return render_template("login.html", error="Invalid username or password.")

    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["role"] = user["role"]
    return redirect(url_for("dashboard_page"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login_page"))


@app.route("/api/v1/auth/login", methods=["POST"])
def api_login():
    """JSON login for API clients (Postman, mobile app, etc). Returns a JWT
    instead of setting a session cookie - this is what you'd call from a
    non-browser client that can't store cookies."""
    data = request.get_json(force=True)
    username = (data.get("username") or "").strip()
    password = data.get("password", "")

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if user is None or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "Invalid username or password"}), 401

    token = generate_jwt(user["id"], user["username"], user["role"])
    return jsonify({
        "token": token,
        "expires_in_hours": JWT_EXPIRY_HOURS,
        "user": {"id": user["id"], "username": user["username"], "role": user["role"]},
    })


@app.route("/api/v1/users/<int:target_user_id>/role", methods=["PATCH"])
@login_required
@role_required("Admin")
def change_user_role(target_user_id):
    """Admin-only: promote/demote another user's role. Demonstrates a real
    admin-management action gated by RBAC."""
    data = request.get_json(force=True)
    new_role = data.get("role")
    if new_role not in ("Admin", "Accountant", "Sales Staff"):
        return jsonify({"error": "Invalid role"}), 400

    db = get_db()
    # CRITICAL: must also check org_id, not just the user id - otherwise
    # an Admin from Organization A could change the role of a user in
    # Organization B just by guessing their user id. This is exactly the
    # kind of cross-tenant bug multi-tenant systems have to guard against
    # on every single query that touches another table's row by id.
    cur = db.execute(
        "UPDATE users SET role = ? WHERE id = ? AND org_id = ?",
        (new_role, target_user_id, current_org_id()),
    )
    if cur.rowcount == 0:
        db.rollback()
        return jsonify({"error": "User not found"}), 404
    log_audit(db, "CHANGE_USER_ROLE", "user", target_user_id, f"Changed role to {new_role}")
    db.commit()
    return jsonify({"success": True})


@app.route("/api/v1/organizations/invite", methods=["POST"])
@login_required
@role_required("Admin")
def invite_teammate():
    """Admin-only: create a new user account already assigned to the
    Admin's own organization. This is how a company adds Accountants and
    Sales Staff to their SHARED data - not via public self-registration
    (which always creates a brand new, separate organization instead)."""
    data = request.get_json(force=True)
    username = (data.get("username") or "").strip()
    password = data.get("password", "")
    role = data.get("role", "Sales Staff")

    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400
    if role not in ("Admin", "Accountant", "Sales Staff"):
        return jsonify({"error": "Invalid role"}), 400

    db = get_db()
    existing = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if existing:
        return jsonify({"error": "That username is already taken"}), 400

    password_hash = generate_password_hash(password)
    cur = db.execute(
        "INSERT INTO users (org_id, username, password_hash, role) VALUES (?, ?, ?, ?)",
        (current_org_id(), username, password_hash, role),
    )
    log_audit(db, "INVITE_TEAMMATE", "user", cur.lastrowid, f"Invited '{username}' as {role}")
    db.commit()
    return jsonify({"id": cur.lastrowid, "username": username, "role": role}), 201


@app.route("/api/v1/organizations/users", methods=["GET"])
@login_required
def list_org_users():
    """Everyone in the org can see their teammates (name + role) - useful
    for the UI, and there's nothing sensitive in a username/role pairing."""
    db = get_db()
    rows = db.execute(
        "SELECT id, username, role, created_at FROM users WHERE org_id = ? ORDER BY username",
        (current_org_id(),),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------
# Page routes
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
# All authenticated roles can view. Only Admin can delete (RBAC example).
# ---------------------------------------------------------------
@app.route("/api/customers", methods=["GET"])
@login_required
def get_customers():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM customers WHERE org_id = ? ORDER BY name COLLATE NOCASE",
        (current_org_id(),),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/customers/<int:customer_id>", methods=["GET"])
@login_required
def get_customer(customer_id):
    db = get_db()
    row = db.execute(
        "SELECT * FROM customers WHERE id = ? AND org_id = ?",
        (customer_id, current_org_id()),
    ).fetchone()
    if row is None:
        return jsonify({"error": "Customer not found"}), 404
    return jsonify(dict(row))


@app.route("/api/customers/<int:customer_id>/stats", methods=["GET"])
@login_required
def get_customer_stats(customer_id):
    """Phase 5: full billing history summary for one customer."""
    db = get_db()
    customer = db.execute(
        "SELECT * FROM customers WHERE id = ? AND org_id = ?", (customer_id, current_org_id())
    ).fetchone()
    if customer is None:
        return jsonify({"error": "Customer not found"}), 404

    invoices = db.execute(
        "SELECT * FROM invoices WHERE customer_id = ? ORDER BY invoice_date DESC", (customer_id,)
    ).fetchall()

    total_billed = sum(inv["total"] for inv in invoices)
    total_paid = sum(get_amount_paid(db, inv["id"]) for inv in invoices)

    return jsonify({
        "customer": dict(customer),
        "total_invoices": len(invoices),
        "total_billed": round(total_billed, 2),
        "total_paid": round(total_paid, 2),
        "outstanding": round(total_billed - total_paid, 2),
        "invoices": [dict(inv) for inv in invoices],
    })


@app.route("/api/customers", methods=["POST"])
@login_required
def add_customer():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400

    db = get_db()
    cur = db.execute(
        "INSERT INTO customers (org_id, user_id, name, email, phone, address) VALUES (?, ?, ?, ?, ?, ?)",
        (current_org_id(), current_user_id(), name, data.get("email", "").strip(),
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
        "UPDATE customers SET name = ?, email = ?, phone = ?, address = ? WHERE id = ? AND org_id = ?",
        (name, data.get("email", ""), data.get("phone", ""), data.get("address", ""),
         customer_id, current_org_id()),
    )
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "Customer not found"}), 404
    return jsonify({"success": True})


@app.route("/api/customers/<int:customer_id>", methods=["DELETE"])
@login_required
@role_required("Admin")   # <-- RBAC in action: only Admin can delete customers
def delete_customer(customer_id):
    db = get_db()
    customer = db.execute(
        "SELECT name FROM customers WHERE id = ? AND org_id = ?", (customer_id, current_org_id())
    ).fetchone()
    try:
        cur = db.execute(
            "DELETE FROM customers WHERE id = ? AND org_id = ?",
            (customer_id, current_org_id()),
        )
    except sqlite3.IntegrityError:
        db.rollback()
        return jsonify({"error": "Cannot delete a customer that has invoices. Delete their invoices first."}), 400
    if cur.rowcount == 0:
        db.rollback()
        return jsonify({"error": "Customer not found"}), 404
    log_audit(db, "DELETE_CUSTOMER", "customer", customer_id, f"Deleted customer '{customer['name'] if customer else customer_id}'")
    db.commit()
    return jsonify({"success": True})


# ---------------------------------------------------------------
# API: products
# All authenticated roles can VIEW products (Sales Staff needs to see
# stock/prices to build an invoice). Only Admin can create/edit/delete -
# same reasoning as customers: catalog and pricing are sensitive.
# ---------------------------------------------------------------
@app.route("/api/products", methods=["GET"])
@login_required
def get_products():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM products WHERE org_id = ? ORDER BY name COLLATE NOCASE",
        (current_org_id(),),
    ).fetchall()
    products = []
    for r in rows:
        p = dict(r)
        p["is_low_stock"] = p["stock_quantity"] <= p["low_stock_threshold"]
        products.append(p)
    return jsonify(products)


@app.route("/api/products/<int:product_id>", methods=["GET"])
@login_required
def get_product(product_id):
    db = get_db()
    product = get_owned_product(db, product_id, current_org_id())
    if product is None:
        return jsonify({"error": "Product not found"}), 404
    result = dict(product)
    result["is_low_stock"] = result["stock_quantity"] <= result["low_stock_threshold"]
    return jsonify(result)


@app.route("/api/products", methods=["POST"])
@login_required
@role_required("Admin")
def create_product():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    sku = (data.get("sku") or "").strip()
    if not name or not sku:
        return jsonify({"error": "Name and SKU are required"}), 400

    db = get_db()
    try:
        cur = db.execute(
            """INSERT INTO products
               (org_id, user_id, sku, name, description, price, tax_percent,
                stock_quantity, low_stock_threshold, is_active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (current_org_id(), current_user_id(), sku, name, data.get("description", ""),
             float(data.get("price", 0)), float(data.get("tax_percent", 0)),
             int(data.get("stock_quantity", 0)), int(data.get("low_stock_threshold", 5)),
             1 if data.get("is_active", True) else 0),
        )
    except sqlite3.IntegrityError:
        # Fires because of our UNIQUE(org_id, sku) constraint in schema.sql
        return jsonify({"error": f"SKU '{sku}' already exists in your catalog"}), 400

    product_id = cur.lastrowid
    starting_stock = int(data.get("stock_quantity", 0))
    if starting_stock > 0:
        # Record the initial stock as a movement too, so the history is
        # complete from day one, not just from the first sale.
        db.execute(
            "INSERT INTO stock_movements (product_id, change_amount, reason) VALUES (?, ?, ?)",
            (product_id, starting_stock, "Initial stock"),
        )
    db.commit()
    return jsonify({"id": product_id}), 201


@app.route("/api/products/<int:product_id>", methods=["PUT"])
@login_required
@role_required("Admin")
def update_product(product_id):
    db = get_db()
    if get_owned_product(db, product_id, current_org_id()) is None:
        return jsonify({"error": "Product not found"}), 404

    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400

    db.execute(
        """UPDATE products SET name = ?, description = ?, price = ?, tax_percent = ?,
           low_stock_threshold = ?, is_active = ? WHERE id = ?""",
        (name, data.get("description", ""), float(data.get("price", 0)),
         float(data.get("tax_percent", 0)), int(data.get("low_stock_threshold", 5)),
         1 if data.get("is_active", True) else 0, product_id),
    )
    db.commit()
    return jsonify({"success": True})


@app.route("/api/products/<int:product_id>", methods=["DELETE"])
@login_required
@role_required("Admin")
def delete_product(product_id):
    db = get_db()
    if get_owned_product(db, product_id, current_org_id()) is None:
        return jsonify({"error": "Product not found"}), 404
    db.execute("DELETE FROM products WHERE id = ?", (product_id,))
    db.commit()
    return jsonify({"success": True})


@app.route("/api/products/<int:product_id>/adjust-stock", methods=["POST"])
@login_required
@role_required("Admin", "Accountant")
def adjust_product_stock(product_id):
    """Manual stock correction - e.g. restocking, damage write-off, a
    physical inventory count correction. Always goes through adjust_stock()
    so it's logged exactly like an automatic sale-driven change."""
    db = get_db()
    product = get_owned_product(db, product_id, current_org_id())
    if product is None:
        return jsonify({"error": "Product not found"}), 404

    data = request.get_json(force=True)
    try:
        change = int(data.get("change_amount"))
    except (TypeError, ValueError):
        return jsonify({"error": "change_amount must be a whole number (positive or negative)"}), 400
    reason = (data.get("reason") or "Manual adjustment").strip()

    if product["stock_quantity"] + change < 0:
        return jsonify({"error": "This would make stock negative - not allowed"}), 400

    adjust_stock(db, product_id, change, reason)
    db.commit()
    return jsonify({"success": True})


@app.route("/api/products/<int:product_id>/stock-history", methods=["GET"])
@login_required
def get_stock_history(product_id):
    db = get_db()
    if get_owned_product(db, product_id, current_org_id()) is None:
        return jsonify({"error": "Product not found"}), 404
    rows = db.execute(
        "SELECT * FROM stock_movements WHERE product_id = ? ORDER BY created_at DESC, id DESC",
        (product_id,),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------
# API: invoices
# ---------------------------------------------------------------
def build_invoice_query(org_id, search, status):
    query = """
        SELECT invoices.*, customers.name AS customer_name
        FROM invoices
        JOIN customers ON invoices.customer_id = customers.id
        WHERE customers.org_id = ?
    """
    params = [org_id]
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
    db = get_db()
    mark_overdue_invoices(db, current_org_id())

    search = request.args.get("search", "").strip()
    status = request.args.get("status", "").strip()
    page = max(int(request.args.get("page", 1)), 1)
    limit = min(max(int(request.args.get("limit", 20)), 1), 100)  # cap at 100 to prevent abuse
    offset = (page - 1) * limit

    query, params = build_invoice_query(current_org_id(), search, status)
    count_row = db.execute(query.replace("SELECT invoices.*, customers.name AS customer_name", "SELECT COUNT(*) AS c"), params).fetchone()
    total_count = count_row["c"]

    query += " ORDER BY invoices.invoice_date DESC, invoices.id DESC LIMIT ? OFFSET ?"
    params_with_page = params + [limit, offset]

    rows = db.execute(query, params_with_page).fetchall()
    invoices = []
    for r in rows:
        inv = dict(r)
        paid = get_amount_paid(db, inv["id"])
        inv["amount_paid"] = paid
        inv["amount_due"] = round(inv["total"] - paid, 2)
        invoices.append(inv)

    return jsonify({
        "invoices": invoices,
        "page": page,
        "limit": limit,
        "total_count": total_count,
        "total_pages": (total_count + limit - 1) // limit if total_count else 1,
    })


@app.route("/api/invoices/<int:invoice_id>", methods=["GET"])
@login_required
def get_invoice(invoice_id):
    db = get_db()
    invoice = get_owned_invoice(db, invoice_id, current_org_id())
    if invoice is None:
        return jsonify({"error": "Invoice not found"}), 404

    items = db.execute("SELECT * FROM invoice_items WHERE invoice_id = ?", (invoice_id,)).fetchall()
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
    owned = db.execute(
        "SELECT id FROM customers WHERE id = ? AND org_id = ?", (customer_id, current_org_id())
    ).fetchone()
    if owned is None:
        return jsonify({"error": "Invalid customer"}), 400

    # Validate stock BEFORE writing anything. If item #3 doesn't have
    # enough stock, we want to fail the whole request - not create the
    # invoice and then discover partway through that we can't fulfill it.
    for item in items:
        product_id = item.get("product_id")
        if product_id:
            product = get_owned_product(db, product_id, current_org_id())
            if product is None:
                return jsonify({"error": f"Invalid product_id {product_id}"}), 400
            if product["stock_quantity"] < item["quantity"]:
                return jsonify({
                    "error": f"Not enough stock for '{product['name']}': "
                             f"have {product['stock_quantity']}, need {item['quantity']}"
                }), 400

    tax_percent = data.get("tax_percent", 0)
    subtotal, tax_amount, total = recalculate_totals(items, tax_percent)
    invoice_number = generate_invoice_number(db)

    cur = db.execute(
        """INSERT INTO invoices
           (invoice_number, customer_id, invoice_date, due_date, tax_percent, subtotal, tax_amount, total, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (invoice_number, customer_id, data.get("invoice_date"), data.get("due_date"),
         tax_percent, subtotal, tax_amount, total, data.get("status", "Draft")),
    )
    invoice_id = cur.lastrowid

    for item in items:
        product_id = item.get("product_id")
        db.execute(
            "INSERT INTO invoice_items (invoice_id, product_id, item_name, quantity, price) VALUES (?, ?, ?, ?, ?)",
            (invoice_id, product_id, item["item_name"], item["quantity"], item["price"]),
        )
        if product_id:
            # Stock goes DOWN (negative change) because this invoice sold it.
            adjust_stock(db, product_id, -item["quantity"], f"Invoice #{invoice_id} created")

    notify(db, current_user_id(), "invoice_created", f"Invoice {invoice_number} created for ₹{total:.2f}")
    db.commit()
    return jsonify({"id": invoice_id}), 201


@app.route("/api/invoices/<int:invoice_id>", methods=["PUT"])
@login_required
def update_invoice(invoice_id):
    db = get_db()
    if get_owned_invoice(db, invoice_id, current_org_id()) is None:
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
        (data.get("customer_id"), data.get("invoice_date"), data.get("due_date"),
         tax_percent, subtotal, tax_amount, total, data.get("status", "Pending"), invoice_id),
    )

    # Undo stock effects of the OLD line items before applying the new ones.
    # Editing an invoice from "3 units" down to "1 unit" should give 2 units
    # back to stock - and if it's re-linked to a different product entirely,
    # the old product's stock needs restoring while the new one gets deducted.
    old_items = db.execute(
        "SELECT product_id, quantity FROM invoice_items WHERE invoice_id = ?", (invoice_id,)
    ).fetchall()
    for old_item in old_items:
        if old_item["product_id"]:
            adjust_stock(db, old_item["product_id"], old_item["quantity"], f"Invoice #{invoice_id} edited (reverting old item)")

    db.execute("DELETE FROM invoice_items WHERE invoice_id = ?", (invoice_id,))

    for item in items:
        product_id = item.get("product_id")
        db.execute(
            "INSERT INTO invoice_items (invoice_id, product_id, item_name, quantity, price) VALUES (?, ?, ?, ?, ?)",
            (invoice_id, product_id, item["item_name"], item["quantity"], item["price"]),
        )
        if product_id:
            adjust_stock(db, product_id, -item["quantity"], f"Invoice #{invoice_id} edited (applying new item)")

    db.commit()
    return jsonify({"success": True})


@app.route("/api/invoices/<int:invoice_id>/status", methods=["PATCH"])
@login_required
def update_invoice_status(invoice_id):
    db = get_db()
    if get_owned_invoice(db, invoice_id, current_org_id()) is None:
        return jsonify({"error": "Invoice not found"}), 404

    data = request.get_json(force=True)
    status = data.get("status")
    if status not in ("Draft", "Pending", "Paid", "Overdue", "Cancelled"):
        return jsonify({"error": "Invalid status"}), 400
    db.execute("UPDATE invoices SET status = ? WHERE id = ?", (status, invoice_id))
    db.commit()
    return jsonify({"success": True})


@app.route("/api/invoices/<int:invoice_id>", methods=["DELETE"])
@login_required
@role_required("Admin")   # only Admin can delete invoices
def delete_invoice(invoice_id):
    db = get_db()
    invoice = get_owned_invoice(db, invoice_id, current_org_id())
    if invoice is None:
        return jsonify({"error": "Invoice not found"}), 404

    items = db.execute(
        "SELECT product_id, quantity FROM invoice_items WHERE invoice_id = ?", (invoice_id,)
    ).fetchall()
    for item in items:
        if item["product_id"]:
            adjust_stock(db, item["product_id"], item["quantity"], f"Invoice #{invoice_id} deleted")

    db.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))
    log_audit(db, "DELETE_INVOICE", "invoice", invoice_id,
              f"Deleted invoice {invoice['invoice_number'] or invoice_id} (₹{invoice['total']:.2f})")
    db.commit()
    return jsonify({"success": True})


# ---------------------------------------------------------------
# API: payments - Admin or Accountant only (Sales Staff can view invoices
# but shouldn't be recording money received - a realistic RBAC boundary)
# ---------------------------------------------------------------
@app.route("/api/invoices/<int:invoice_id>/payments", methods=["POST"])
@login_required
@role_required("Admin", "Accountant")
def add_payment(invoice_id):
    db = get_db()
    invoice = get_owned_invoice(db, invoice_id, current_org_id())
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
    payment_method = (data.get("payment_method") or "Cash").strip()
    reference_id = (data.get("reference_id") or "").strip()
    note = (data.get("note") or "").strip()

    cur = db.execute(
        "INSERT INTO payments (invoice_id, amount, paid_on, payment_method, reference_id, note) VALUES (?, ?, ?, ?, ?, ?)",
        (invoice_id, amount, paid_on, payment_method, reference_id, note),
    )
    payment_id = cur.lastrowid

    total_paid = get_amount_paid(db, invoice_id)
    if total_paid >= invoice["total"]:
        db.execute("UPDATE invoices SET status = 'Paid' WHERE id = ?", (invoice_id,))

    log_audit(db, "RECORD_PAYMENT", "payment", payment_id,
              f"Recorded ₹{amount:.2f} ({payment_method}) on invoice {invoice['invoice_number'] or invoice_id}")
    notify(db, current_user_id(), "payment_received",
           f"Payment of ₹{amount:.2f} received for invoice {invoice['invoice_number'] or invoice_id}")

    db.commit()
    return jsonify({"success": True, "amount_paid": total_paid,
                     "amount_due": round(invoice["total"] - total_paid, 2)}), 201


@app.route("/api/payments/<int:payment_id>", methods=["DELETE"])
@login_required
@role_required("Admin", "Accountant")
def delete_payment(payment_id):
    db = get_db()
    row = db.execute(
        """SELECT payments.id, payments.invoice_id FROM payments
           JOIN invoices ON payments.invoice_id = invoices.id
           JOIN customers ON invoices.customer_id = customers.id
           WHERE payments.id = ? AND customers.org_id = ?""",
        (payment_id, current_org_id()),
    ).fetchone()
    if row is None:
        return jsonify({"error": "Payment not found"}), 404

    db.execute("DELETE FROM payments WHERE id = ?", (payment_id,))
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
    org_id = current_org_id()
    mark_overdue_invoices(db, org_id)

    def scalar(sql, params):
        return db.execute(sql, params).fetchone()[0]

    base = "FROM invoices JOIN customers ON invoices.customer_id = customers.id WHERE customers.org_id = ?"

    return jsonify({
        "total_revenue": scalar(f"SELECT COALESCE(SUM(total), 0) {base} AND invoices.status = 'Paid'", (org_id,)),
        "total_pending": scalar(f"SELECT COALESCE(SUM(total), 0) {base} AND invoices.status = 'Pending'", (org_id,)),
        "overdue_amount": scalar(f"SELECT COALESCE(SUM(total), 0) {base} AND invoices.status = 'Overdue'", (org_id,)),
        "overdue_count": scalar(f"SELECT COUNT(*) {base} AND invoices.status = 'Overdue'", (org_id,)),
        "total_invoices": scalar(f"SELECT COUNT(*) {base}", (org_id,)),
    })


@app.route("/api/dashboard/revenue-by-month", methods=["GET"])
@login_required
def revenue_by_month():
    """Phase 11: data for the analytics chart - paid revenue grouped by
    calendar month, most recent 6 months. strftime pulls YYYY-MM out of the
    stored date string directly in SQL rather than in Python."""
    db = get_db()
    rows = db.execute(
        """SELECT strftime('%Y-%m', invoices.invoice_date) AS month, SUM(invoices.total) AS revenue
           FROM invoices JOIN customers ON invoices.customer_id = customers.id
           WHERE customers.org_id = ? AND invoices.status = 'Paid'
           GROUP BY month ORDER BY month DESC LIMIT 6""",
        (current_org_id(),),
    ).fetchall()
    data = [{"month": r["month"], "revenue": round(r["revenue"], 2)} for r in rows]
    data.reverse()  # chronological order for the chart, oldest to newest
    return jsonify(data)


# ---------------------------------------------------------------
# API: audit log (Phase 7) - Admin only, read-only
# ---------------------------------------------------------------
@app.route("/api/audit-log", methods=["GET"])
@login_required
@role_required("Admin")
def get_audit_log():
    db = get_db()
    page = max(int(request.args.get("page", 1)), 1)
    limit = min(max(int(request.args.get("limit", 50)), 1), 200)
    rows = db.execute(
        "SELECT * FROM audit_log WHERE org_id = ? ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        (current_org_id(), limit, (page - 1) * limit),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------
# API: notifications (Phase 8)
# ---------------------------------------------------------------
@app.route("/api/notifications", methods=["GET"])
@login_required
def get_notifications():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC LIMIT 50",
        (current_user_id(),),
    ).fetchall()
    unread_count = db.execute(
        "SELECT COUNT(*) AS c FROM notifications WHERE user_id = ? AND is_read = 0", (current_user_id(),)
    ).fetchone()["c"]
    return jsonify({"notifications": [dict(r) for r in rows], "unread_count": unread_count})


@app.route("/api/notifications/<int:notif_id>/read", methods=["PATCH"])
@login_required
def mark_notification_read(notif_id):
    db = get_db()
    cur = db.execute(
        "UPDATE notifications SET is_read = 1 WHERE id = ? AND user_id = ?", (notif_id, current_user_id())
    )
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "Notification not found"}), 404
    return jsonify({"success": True})


# ---------------------------------------------------------------
# API: recurring invoices (Phase 10)
# No Redis/cron here - "next_invoice_date" is checked and generated when
# /api/recurring/run is called. In production this endpoint would be hit
# by a scheduled job (cron, or a Render/GitHub Actions scheduled trigger)
# once a day; for this project it can be triggered manually or by any
# simple external scheduler that pings this URL.
# ---------------------------------------------------------------
@app.route("/api/recurring", methods=["GET"])
@login_required
def get_recurring_rules():
    db = get_db()
    rows = db.execute(
        """SELECT recurring_rules.*, customers.name AS customer_name
           FROM recurring_rules JOIN customers ON recurring_rules.customer_id = customers.id
           WHERE recurring_rules.org_id = ? ORDER BY next_invoice_date""",
        (current_org_id(),),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/recurring", methods=["POST"])
@login_required
@role_required("Admin", "Accountant")
def create_recurring_rule():
    data = request.get_json(force=True)
    required = ["customer_id", "frequency", "item_name", "quantity", "price", "next_invoice_date"]
    if not all(data.get(f) for f in required):
        return jsonify({"error": f"Required fields: {', '.join(required)}"}), 400
    if data["frequency"] not in ("Monthly", "Quarterly", "Yearly"):
        return jsonify({"error": "frequency must be Monthly, Quarterly, or Yearly"}), 400

    db = get_db()
    owned = db.execute(
        "SELECT id FROM customers WHERE id = ? AND org_id = ?", (data["customer_id"], current_org_id())
    ).fetchone()
    if owned is None:
        return jsonify({"error": "Invalid customer"}), 400

    cur = db.execute(
        """INSERT INTO recurring_rules
           (org_id, user_id, customer_id, frequency, item_name, quantity, price, tax_percent, next_invoice_date)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (current_org_id(), current_user_id(), data["customer_id"], data["frequency"], data["item_name"],
         data["quantity"], data["price"], data.get("tax_percent", 0), data["next_invoice_date"]),
    )
    db.commit()
    return jsonify({"id": cur.lastrowid}), 201


@app.route("/api/recurring/<int:rule_id>", methods=["DELETE"])
@login_required
@role_required("Admin", "Accountant")
def delete_recurring_rule(rule_id):
    db = get_db()
    cur = db.execute(
        "DELETE FROM recurring_rules WHERE id = ? AND org_id = ?", (rule_id, current_org_id())
    )
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "Rule not found"}), 404
    return jsonify({"success": True})


def advance_date(date_str, frequency):
    """Bump a date forward by one billing cycle. Kept as a plain function
    (not a method) so it's easy to unit test in isolation."""
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    if frequency == "Monthly":
        month = d.month + 1
        year = d.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        day = min(d.day, 28)  # sidesteps Feb 30th-type issues; simple and safe
        return date(year, month, day).isoformat()
    if frequency == "Quarterly":
        for _ in range(3):
            date_str = advance_date(date_str, "Monthly")
        return date_str
    if frequency == "Yearly":
        return date(d.year + 1, d.month, min(d.day, 28)).isoformat()
    return date_str


@app.route("/api/recurring/run", methods=["POST"])
@login_required
@role_required("Admin", "Accountant")
def run_recurring_invoices():
    """Generates an invoice for every active rule whose next_invoice_date
    has arrived, then advances that rule to its next date. Safe to call
    repeatedly - a rule only fires once its date has actually passed."""
    db = get_db()
    today = date.today().isoformat()
    due_rules = db.execute(
        "SELECT * FROM recurring_rules WHERE org_id = ? AND is_active = 1 AND next_invoice_date <= ?",
        (current_org_id(), today),
    ).fetchall()

    generated = []
    for rule in due_rules:
        items = [{"item_name": rule["item_name"], "quantity": rule["quantity"], "price": rule["price"]}]
        subtotal, tax_amount, total = recalculate_totals(items, rule["tax_percent"])
        invoice_number = generate_invoice_number(db)
        due = advance_date(today, "Monthly")  # simple default: due in ~1 month
        cur = db.execute(
            """INSERT INTO invoices (invoice_number, customer_id, invoice_date, due_date,
               tax_percent, subtotal, tax_amount, total, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Pending')""",
            (invoice_number, rule["customer_id"], today, due, rule["tax_percent"], subtotal, tax_amount, total),
        )
        invoice_id = cur.lastrowid
        db.execute(
            "INSERT INTO invoice_items (invoice_id, item_name, quantity, price) VALUES (?, ?, ?, ?)",
            (invoice_id, rule["item_name"], rule["quantity"], rule["price"]),
        )
        new_next_date = advance_date(rule["next_invoice_date"], rule["frequency"])
        db.execute("UPDATE recurring_rules SET next_invoice_date = ? WHERE id = ?", (new_next_date, rule["id"]))
        notify(db, current_user_id(), "invoice_created", f"Recurring invoice {invoice_number} auto-generated")
        generated.append(invoice_number)

    db.commit()
    return jsonify({"generated": generated, "count": len(generated)})


# ---------------------------------------------------------------
# API: AI business assistant (Phase 13)
# Deliberately NOT a free-text-to-SQL system. A real LLM asked to write
# arbitrary SQL against a production database is a serious injection/
# data-exfiltration risk - the prompt explicitly warns against this.
# Instead: match the question to one of a small set of SAFE, predefined,
# parameterized queries. This is the "safe query/intent layer" pattern -
# the model (or here, simple keyword matching) only ever picks WHICH
# pre-approved query to run, never WHAT SQL to run.
# ---------------------------------------------------------------
@app.route("/api/assistant/ask", methods=["POST"])
@login_required
def ask_assistant():
    data = request.get_json(force=True)
    question = (data.get("question") or "").lower()
    db = get_db()
    org_id = current_org_id()

    if "overdue" in question:
        rows = db.execute(
            """SELECT customers.name, invoices.invoice_number, invoices.total FROM invoices
               JOIN customers ON invoices.customer_id = customers.id
               WHERE customers.org_id = ? AND invoices.status = 'Overdue'""", (org_id,)
        ).fetchall()
        if not rows:
            return jsonify({"answer": "No overdue invoices right now.", "data": []})
        lines = [f"{r['customer_name'] if 'customer_name' in r.keys() else r['name']} owes ₹{r['total']:.2f} on {r['invoice_number']}" for r in rows]
        return jsonify({"answer": f"You have {len(rows)} overdue invoice(s): " + "; ".join(lines), "data": [dict(r) for r in rows]})

    if "revenue" in question and ("last month" in question or "this month" in question):
        target_month = date.today().strftime("%Y-%m") if "this month" in question else \
            (date.today().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        row = db.execute(
            """SELECT COALESCE(SUM(invoices.total), 0) AS revenue FROM invoices
               JOIN customers ON invoices.customer_id = customers.id
               WHERE customers.org_id = ? AND invoices.status = 'Paid'
                 AND strftime('%Y-%m', invoices.invoice_date) = ?""",
            (org_id, target_month),
        ).fetchone()
        return jsonify({"answer": f"Revenue for {target_month}: ₹{row['revenue']:.2f}", "data": {"month": target_month, "revenue": row["revenue"]}})

    if "product" in question and ("most revenue" in question or "best selling" in question or "top" in question):
        rows = db.execute(
            """SELECT products.name, SUM(invoice_items.quantity * invoice_items.price) AS revenue
               FROM invoice_items JOIN products ON invoice_items.product_id = products.id
               WHERE products.org_id = ? GROUP BY products.id ORDER BY revenue DESC LIMIT 1""",
            (org_id,),
        ).fetchone()
        if rows is None:
            return jsonify({"answer": "No product sales recorded yet.", "data": None})
        return jsonify({"answer": f"'{rows['name']}' generated the most revenue: ₹{rows['revenue']:.2f}", "data": dict(rows)})

    if "above" in question or "over" in question:
        import re
        match = re.search(r"[₹$]?\s*(\d[\d,]*)", question)
        threshold = float(match.group(1).replace(",", "")) if match else 0
        rows = db.execute(
            """SELECT invoices.invoice_number, invoices.total, customers.name FROM invoices
               JOIN customers ON invoices.customer_id = customers.id
               WHERE customers.org_id = ? AND invoices.total > ?""",
            (org_id, threshold),
        ).fetchall()
        return jsonify({"answer": f"{len(rows)} invoice(s) above ₹{threshold:.0f}", "data": [dict(r) for r in rows]})

    return jsonify({
        "answer": "I can currently answer questions about: overdue invoices, revenue this/last month, "
                   "best-selling products, and invoices above a given amount. Try rephrasing your question "
                   "around one of those topics.",
        "data": None,
    })


# ---------------------------------------------------------------
# API: payment risk scoring (Phase 14)
# A rule-based heuristic, NOT a trained ML model - there's no historical
# dataset here to train one on. This is deliberately structured the same
# way a real model's output would be consumed (a 0-100 score + reasons),
# so swapping this function for a trained model later (e.g. scikit-learn
# logistic regression) wouldn't require changing anything else.
# ---------------------------------------------------------------
@app.route("/api/customers/<int:customer_id>/risk-score", methods=["GET"])
@login_required
def get_customer_risk_score(customer_id):
    db = get_db()
    customer = db.execute(
        "SELECT * FROM customers WHERE id = ? AND user_id = ?", (customer_id, current_user_id())
    ).fetchone()
    if customer is None:
        return jsonify({"error": "Customer not found"}), 404

    invoices = db.execute("SELECT * FROM invoices WHERE customer_id = ?", (customer_id,)).fetchall()
    total_invoices = len(invoices)
    overdue_count = sum(1 for inv in invoices if inv["status"] == "Overdue")
    avg_amount = sum(inv["total"] for inv in invoices) / total_invoices if total_invoices else 0

    score = 0
    reasons = []
    if total_invoices > 0:
        overdue_ratio = overdue_count / total_invoices
        if overdue_ratio > 0.3:
            score += 40
            reasons.append(f"{overdue_count} of {total_invoices} invoices have been overdue")
        if avg_amount > 50000:
            score += 20
            reasons.append(f"High average invoice amount (₹{avg_amount:.0f})")
        if overdue_count >= 3:
            score += 20
            reasons.append("Multiple overdue invoices on record")
        if total_invoices < 2:
            score += 10
            reasons.append("Limited payment history to judge reliability")
    score = min(score, 100)

    return jsonify({
        "customer_id": customer_id,
        "customer_name": customer["name"],
        "risk_score": score,
        "risk_label": "High" if score >= 60 else "Medium" if score >= 30 else "Low",
        "reasons": reasons or ["No risk indicators found"],
    })


# ---------------------------------------------------------------
# CSV / PDF export (unchanged from before)
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
        writer.writerow([r["id"], r["customer_name"], r["invoice_date"], r["due_date"],
                          r["subtotal"], r["tax_percent"], r["tax_amount"], r["total"],
                          paid, round(r["total"] - paid, 2), r["status"]])

    mem = io.BytesIO(output.getvalue().encode("utf-8"))
    return send_file(mem, mimetype="text/csv", as_attachment=True, download_name="invoices_export.csv")


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
    return send_file(buffer, mimetype="application/pdf", as_attachment=True, download_name=f"invoice_{invoice_id}.pdf")


if __name__ == "__main__":
    app.run(debug=True)