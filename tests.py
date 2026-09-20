"""
pytest suite for BillDesk.

Run with:  pytest tests.py -v

Uses a temporary, isolated SQLite database for every test run (never your
real billing.db), so running tests can never corrupt real data.
"""

import os
import tempfile

import pytest


@pytest.fixture
def client():
    """Fresh app + fresh temp database for every single test function."""
    db_fd, db_path = tempfile.mkstemp()
    os.environ["DB_PATH"] = db_path

    import app as app_module
    app_module.DB_PATH = db_path
    app_module.init_db()

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        yield client

    os.close(db_fd)
    os.unlink(db_path)


def register(client, username="alice", password="secret123"):
    return client.post("/register", data={"username": username, "password": password})


def invite_teammate(client, username, password="secret123", role="Sales Staff"):
    """Adds a teammate to the CURRENT session's organization - this is how
    multiple users end up sharing the same data, unlike /register which
    always creates a brand new organization."""
    return client.post("/api/v1/organizations/invite", json={
        "username": username, "password": password, "role": role,
    })


def make_customer(client, name="Acme Corp"):
    r = client.post("/api/customers", json={"name": name})
    return r.get_json()["id"]


# ---------------------------------------------------------------
# Auth & RBAC
# ---------------------------------------------------------------
def test_registering_creates_a_new_organization_as_admin(client):
    register(client, "alice")
    r = client.get("/api/customers")  # any authenticated call works
    assert r.status_code == 200


def test_invited_teammate_shares_the_same_org_data(client):
    """The core multi-tenant behavior: an Admin invites a teammate, and
    that teammate sees the SAME customers - not a separate empty list."""
    register(client, "alice")
    make_customer(client, "Shared Customer")
    invite_teammate(client, "carol", role="Accountant")

    import app as app_module
    with app_module.app.test_client() as c2:
        c2.post("/login", data={"username": "carol", "password": "secret123"})
        r = c2.get("/api/customers")
        names = [c["name"] for c in r.get_json()]
        assert "Shared Customer" in names  # carol sees alice's data - same org


def test_second_public_registration_gets_own_separate_org(client):
    """Two people who both hit /register (never invited by each other)
    must NOT share data - they run separate companies."""
    register(client, "alice")
    make_customer(client, "Alice's Customer")

    import app as app_module
    with app_module.app.test_client() as c2:
        register(c2, "independent_bob")  # public signup, not an invite
        r = c2.get("/api/customers")
        assert r.get_json() == []  # completely empty - different org entirely


def test_unauthenticated_request_is_rejected(client):
    r = client.get("/api/customers")
    assert r.status_code == 401


def test_sales_staff_cannot_record_payment(client):
    register(client, "alice")
    cid = make_customer(client)
    r = client.post("/api/invoices", json={
        "customer_id": cid, "invoice_date": "2026-07-01", "due_date": "2026-08-01",
        "tax_percent": 0, "items": [{"item_name": "X", "quantity": 1, "price": 100}],
    })
    inv_id = r.get_json()["id"]

    invite_teammate(client, "dave", role="Sales Staff")
    import app as app_module
    with app_module.app.test_client() as c2:
        c2.post("/login", data={"username": "dave", "password": "secret123"})
        r = c2.post(f"/api/invoices/{inv_id}/payments", json={"amount": 50, "paid_on": "2026-07-05"})
        assert r.status_code == 403


def test_admin_cannot_change_role_of_user_in_different_org(client):
    """Cross-tenant security check: an Admin should never be able to
    modify a user who belongs to a DIFFERENT organization, even by
    guessing their numeric user id."""
    register(client, "alice")  # org 1, user id 1, Admin

    import app as app_module
    c2 = app_module.app.test_client()
    register(c2, "eve")  # org 2, user id 2, Admin of her OWN org
    # alice (org 1 Admin) tries to change eve's role (org 2 user)
    r = client.patch("/api/v1/users/2/role", json={"role": "Sales Staff"})
    assert r.status_code == 404  # not found from alice's org's point of view


# ---------------------------------------------------------------
# Invoice totals - the core money math
# ---------------------------------------------------------------
def test_invoice_total_calculation(client):
    register(client)
    cid = make_customer(client)
    r = client.post("/api/invoices", json={
        "customer_id": cid, "invoice_date": "2026-07-01", "due_date": "2026-08-01",
        "tax_percent": 18,
        "items": [{"item_name": "Widget", "quantity": 10, "price": 500}],
    })
    inv_id = r.get_json()["id"]
    invoice = client.get(f"/api/invoices/{inv_id}").get_json()

    assert invoice["subtotal"] == 5000.0
    assert invoice["tax_amount"] == 900.0       # 18% of 5000
    assert invoice["total"] == 5900.0


def test_backend_never_trusts_client_sent_totals(client):
    """Even if a client sends a fake 'total', the server recalculates it
    from the actual line items - this is the whole point of never
    trusting client-side money math."""
    register(client)
    cid = make_customer(client)
    r = client.post("/api/invoices", json={
        "customer_id": cid, "invoice_date": "2026-07-01", "due_date": "2026-08-01",
        "tax_percent": 0, "total": 999999,  # attempted fake total - should be ignored
        "items": [{"item_name": "X", "quantity": 1, "price": 100}],
    })
    invoice = client.get(f"/api/invoices/{r.get_json()['id']}").get_json()
    assert invoice["total"] == 100.0


# ---------------------------------------------------------------
# Overdue detection
# ---------------------------------------------------------------
def test_overdue_detection(client):
    register(client)
    cid = make_customer(client)
    r = client.post("/api/invoices", json={
        "customer_id": cid, "invoice_date": "2026-01-01", "due_date": "2020-01-01",  # long past
        "tax_percent": 0, "status": "Pending",
        "items": [{"item_name": "X", "quantity": 1, "price": 100}],
    })
    inv_id = r.get_json()["id"]
    # Reading the invoice list triggers the lazy overdue check
    client.get("/api/invoices")
    invoice = client.get(f"/api/invoices/{inv_id}").get_json()
    assert invoice["status"] == "Overdue"


# ---------------------------------------------------------------
# Partial payments
# ---------------------------------------------------------------
def test_partial_payment_reduces_amount_due(client):
    register(client)
    cid = make_customer(client)
    r = client.post("/api/invoices", json={
        "customer_id": cid, "invoice_date": "2026-07-01", "due_date": "2026-08-01",
        "tax_percent": 0, "status": "Pending",  # explicitly issued, not left as Draft
        "items": [{"item_name": "X", "quantity": 1, "price": 1000}],
    })
    inv_id = r.get_json()["id"]

    client.post(f"/api/invoices/{inv_id}/payments", json={"amount": 400, "paid_on": "2026-07-05"})
    invoice = client.get(f"/api/invoices/{inv_id}").get_json()
    assert invoice["amount_paid"] == 400.0
    assert invoice["amount_due"] == 600.0
    assert invoice["status"] == "Pending"  # not fully paid yet


def test_full_payment_marks_invoice_paid(client):
    register(client)
    cid = make_customer(client)
    r = client.post("/api/invoices", json={
        "customer_id": cid, "invoice_date": "2026-07-01", "due_date": "2026-08-01",
        "tax_percent": 0, "items": [{"item_name": "X", "quantity": 1, "price": 1000}],
    })
    inv_id = r.get_json()["id"]

    client.post(f"/api/invoices/{inv_id}/payments", json={"amount": 1000, "paid_on": "2026-07-05"})
    invoice = client.get(f"/api/invoices/{inv_id}").get_json()
    assert invoice["status"] == "Paid"
    assert invoice["amount_due"] == 0.0


# ---------------------------------------------------------------
# Inventory / stock
# ---------------------------------------------------------------
def test_invoice_reduces_product_stock(client):
    register(client)
    cid = make_customer(client)
    r = client.post("/api/products", json={"sku": "WID-1", "name": "Widget", "price": 100, "stock_quantity": 20})
    pid = r.get_json()["id"]

    client.post("/api/invoices", json={
        "customer_id": cid, "invoice_date": "2026-07-01", "due_date": "2026-08-01", "tax_percent": 0,
        "items": [{"product_id": pid, "item_name": "Widget", "quantity": 5, "price": 100}],
    })
    product = client.get(f"/api/products/{pid}").get_json()
    assert product["stock_quantity"] == 15


def test_cannot_oversell_stock(client):
    register(client)
    cid = make_customer(client)
    r = client.post("/api/products", json={"sku": "WID-2", "name": "Widget", "price": 100, "stock_quantity": 5})
    pid = r.get_json()["id"]

    r = client.post("/api/invoices", json={
        "customer_id": cid, "invoice_date": "2026-07-01", "due_date": "2026-08-01", "tax_percent": 0,
        "items": [{"product_id": pid, "item_name": "Widget", "quantity": 100, "price": 100}],
    })
    assert r.status_code == 400
    product = client.get(f"/api/products/{pid}").get_json()
    assert product["stock_quantity"] == 5  # unchanged after the failed attempt


def test_deleting_invoice_restores_stock(client):
    register(client)
    cid = make_customer(client)
    r = client.post("/api/products", json={"sku": "WID-3", "name": "Widget", "price": 100, "stock_quantity": 10})
    pid = r.get_json()["id"]

    r = client.post("/api/invoices", json={
        "customer_id": cid, "invoice_date": "2026-07-01", "due_date": "2026-08-01", "tax_percent": 0,
        "items": [{"product_id": pid, "item_name": "Widget", "quantity": 3, "price": 100}],
    })
    inv_id = r.get_json()["id"]
    assert client.get(f"/api/products/{pid}").get_json()["stock_quantity"] == 7

    client.delete(f"/api/invoices/{inv_id}")
    assert client.get(f"/api/products/{pid}").get_json()["stock_quantity"] == 10


# ---------------------------------------------------------------
# Ownership / data isolation between users
# ---------------------------------------------------------------
def test_user_cannot_access_another_users_invoice(client):
    register(client, "alice")
    cid = make_customer(client)
    r = client.post("/api/invoices", json={
        "customer_id": cid, "invoice_date": "2026-07-01", "due_date": "2026-08-01",
        "tax_percent": 0, "items": [{"item_name": "X", "quantity": 1, "price": 100}],
    })
    inv_id = r.get_json()["id"]

    import app as app_module
    with app_module.app.test_client() as c2:
        register(c2, "bob")
        r = c2.get(f"/api/invoices/{inv_id}")
        assert r.status_code == 404  # not 403 - bob shouldn't even know it exists


# ---------------------------------------------------------------
# Foreign key protection
# ---------------------------------------------------------------
def test_cannot_delete_customer_with_invoices(client):
    register(client)
    cid = make_customer(client)
    client.post("/api/invoices", json={
        "customer_id": cid, "invoice_date": "2026-07-01", "due_date": "2026-08-01",
        "tax_percent": 0, "items": [{"item_name": "X", "quantity": 1, "price": 100}],
    })
    r = client.delete(f"/api/customers/{cid}")
    assert r.status_code == 400


# ---------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------
def test_deleting_customer_creates_audit_entry(client):
    register(client)
    cid = make_customer(client, "ToDelete")
    client.delete(f"/api/customers/{cid}")
    log = client.get("/api/audit-log").get_json()
    assert any(entry["action"] == "DELETE_CUSTOMER" for entry in log)


# ---------------------------------------------------------------
# Recurring invoices
# ---------------------------------------------------------------
def test_recurring_rule_generates_invoice_when_due(client):
    register(client)
    cid = make_customer(client)
    client.post("/api/recurring", json={
        "customer_id": cid, "frequency": "Monthly", "item_name": "Hosting",
        "quantity": 1, "price": 999, "tax_percent": 0, "next_invoice_date": "2020-01-01",
    })
    r = client.post("/api/recurring/run")
    assert r.get_json()["count"] == 1
