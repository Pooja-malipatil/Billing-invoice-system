PRAGMA foreign_keys = ON;

-- ==========================================================
-- USERS
-- ==========================================================
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ==========================================================
-- CUSTOMERS
-- ==========================================================
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    email TEXT UNIQUE,
    phone TEXT,
    address TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (user_id)
        REFERENCES users(id)
        ON DELETE CASCADE
);

-- ==========================================================
-- INVOICES
-- ==========================================================
CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL,

    invoice_date TEXT NOT NULL
        CHECK(invoice_date GLOB '????-??-??'),

    due_date TEXT NOT NULL
        CHECK(due_date GLOB '????-??-??')
        CHECK(due_date >= invoice_date),

    tax_percent REAL NOT NULL DEFAULT 0
        CHECK(tax_percent >= 0 AND tax_percent <= 100),

    subtotal REAL NOT NULL DEFAULT 0
        CHECK(subtotal >= 0),

    tax_amount REAL NOT NULL DEFAULT 0
        CHECK(tax_amount >= 0),

    total REAL NOT NULL DEFAULT 0
        CHECK(total >= 0),

    status TEXT NOT NULL DEFAULT 'Pending'
        CHECK(status IN ('Paid', 'Pending', 'Overdue')),

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (customer_id)
        REFERENCES customers(id)
);

-- ==========================================================
-- INVOICE ITEMS
-- ==========================================================
CREATE TABLE IF NOT EXISTS invoice_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL,

    item_name TEXT NOT NULL,

    quantity REAL NOT NULL
        CHECK(quantity > 0),

    price REAL NOT NULL
        CHECK(price >= 0),

    FOREIGN KEY (invoice_id)
        REFERENCES invoices(id)
        ON DELETE CASCADE
);

-- ==========================================================
-- PAYMENTS
-- ==========================================================
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    invoice_id INTEGER NOT NULL,

    amount REAL NOT NULL
        CHECK(amount > 0),

    paid_on TEXT NOT NULL
        CHECK(paid_on GLOB '????-??-??'),

    note TEXT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (invoice_id)
        REFERENCES invoices(id)
        ON DELETE CASCADE
);

-- ==========================================================
-- INDEXES
-- ==========================================================
CREATE INDEX IF NOT EXISTS idx_customers_user
ON customers(user_id);

CREATE INDEX IF NOT EXISTS idx_invoices_customer
ON invoices(customer_id);

CREATE INDEX IF NOT EXISTS idx_invoices_status
ON invoices(status);

CREATE INDEX IF NOT EXISTS idx_items_invoice
ON invoice_items(invoice_id);

CREATE INDEX IF NOT EXISTS idx_payments_invoice
ON payments(invoice_id);