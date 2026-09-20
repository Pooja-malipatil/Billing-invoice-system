PRAGMA foreign_keys = ON;

-- -1) ORGANIZATIONS -------------------------------------------------
-- The tenant boundary. Every user belongs to exactly one organization.
-- Data (customers, products, recurring rules) belongs to the ORGANIZATION,
-- not to an individual user - so an Admin, an Accountant, and a Sales
-- Staff member at the same company all see the SAME shared data, with
-- their role controlling what actions each can take on it. This is what
-- makes RBAC actually meaningful: before this, each user had their own
-- private data silo, which made roles pointless (an Accountant had
-- nothing to be an Accountant OF).
CREATE TABLE IF NOT EXISTS organizations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 0) USERS ---------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id         INTEGER NOT NULL,
    username       TEXT NOT NULL UNIQUE,
    password_hash  TEXT NOT NULL,
    role           TEXT NOT NULL DEFAULT 'Sales Staff'
                   CHECK (role IN ('Admin', 'Accountant', 'Sales Staff')),
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (org_id) REFERENCES organizations(id) ON DELETE CASCADE
);

-- 1) CUSTOMERS -------------------------------------------------
-- org_id is the REAL ownership/access boundary now. user_id is kept only
-- as "created_by" - useful to know who added a record, but NOT used for
-- access control anymore (that would defeat the point of shared org data).
CREATE TABLE IF NOT EXISTS customers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id      INTEGER NOT NULL,
    user_id     INTEGER,             -- created_by, informational only
    name        TEXT NOT NULL,
    email       TEXT,
    phone       TEXT,
    address     TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (org_id) REFERENCES organizations(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
);

-- 2) INVOICES ----------------------------------------------------
CREATE TABLE IF NOT EXISTS invoices (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_number TEXT UNIQUE,        -- e.g. INV-2026-001, generated on create
    customer_id   INTEGER NOT NULL,
    invoice_date  TEXT NOT NULL,
    due_date      TEXT NOT NULL,
    tax_percent   REAL NOT NULL DEFAULT 0,
    subtotal      REAL NOT NULL DEFAULT 0,
    tax_amount    REAL NOT NULL DEFAULT 0,
    total         REAL NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'Draft'
                  CHECK (status IN ('Draft', 'Pending', 'Paid', 'Overdue', 'Cancelled')),
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (customer_id) REFERENCES customers(id)
);

-- 3) INVOICE_ITEMS ----------------------------------------------
CREATE TABLE IF NOT EXISTS invoice_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id   INTEGER NOT NULL,
    product_id   INTEGER,             -- nullable: NULL means a freeform line item not tied to inventory
    item_name    TEXT NOT NULL,
    quantity     REAL NOT NULL,
    price        REAL NOT NULL,
    FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE SET NULL
    -- ON DELETE SET NULL: if a product is deleted later, past invoices keep
    -- their line item text/price - they just lose the link to the (now
    -- gone) product. Historical invoices must never break.
);

-- 4) PAYMENTS -----------------------------------------------------
CREATE TABLE IF NOT EXISTS payments (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id     INTEGER NOT NULL,
    amount         REAL NOT NULL,
    paid_on        TEXT NOT NULL,
    payment_method TEXT DEFAULT 'Cash',
    reference_id   TEXT,
    note           TEXT,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
);

-- 5) PRODUCTS -----------------------------------------------------
-- Each user has their own product catalog (same ownership pattern as
-- customers - scoped by user_id, checked on every query).
CREATE TABLE IF NOT EXISTS products (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id              INTEGER NOT NULL,
    user_id             INTEGER,     -- created_by, informational only
    sku                 TEXT NOT NULL,
    name                TEXT NOT NULL,
    description         TEXT,
    price               REAL NOT NULL DEFAULT 0,
    tax_percent         REAL NOT NULL DEFAULT 0,
    stock_quantity      INTEGER NOT NULL DEFAULT 0,
    low_stock_threshold INTEGER NOT NULL DEFAULT 5,
    is_active           INTEGER NOT NULL DEFAULT 1,  -- SQLite has no boolean type; 1=true, 0=false
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (org_id) REFERENCES organizations(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
    UNIQUE (org_id, sku)  -- SKU unique within one org's catalog, not globally
);

-- 6) STOCK_MOVEMENTS -------------------------------------------------
-- An append-only log: we NEVER just overwrite stock_quantity silently.
-- Every change - whether from a sale or a manual correction - gets a row
-- here explaining what happened. This is what "inventory history" means
-- in a real system: you can always answer "why is stock at this number?"
CREATE TABLE IF NOT EXISTS stock_movements (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id     INTEGER NOT NULL,
    change_amount  INTEGER NOT NULL,   -- negative = stock went down, positive = went up
    reason         TEXT NOT NULL,      -- e.g. 'Invoice #12', 'Manual restock', 'Correction'
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);

-- Indexes to keep filtered/joined queries fast as data grows
-- 7) AUDIT LOG -----------------------------------------------------
-- WHO did WHAT to WHICH record, WHEN. Append-only, never edited or deleted.
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id      INTEGER NOT NULL,     -- so an Admin only ever sees THEIR org's
                                       -- audit trail, never another tenant's
    user_id     INTEGER NOT NULL,
    username    TEXT NOT NULL,       -- denormalized on purpose: if the user
                                       -- account is later deleted, the log
                                       -- entry still says who did it
    action      TEXT NOT NULL,       -- e.g. 'DELETE_CUSTOMER', 'RECORD_PAYMENT'
    entity      TEXT NOT NULL,       -- e.g. 'customer', 'invoice', 'payment'
    entity_id   INTEGER,
    details     TEXT,                -- short human-readable summary
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 8) NOTIFICATIONS -----------------------------------------------------
CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    type        TEXT NOT NULL,        -- 'invoice_created','payment_received','overdue','low_stock'
    message     TEXT NOT NULL,
    is_read     INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 9) RECURRING INVOICE RULES -----------------------------------------------------
CREATE TABLE IF NOT EXISTS recurring_rules (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id            INTEGER NOT NULL,
    user_id           INTEGER,      -- created_by, informational only
    customer_id       INTEGER NOT NULL,
    frequency         TEXT NOT NULL CHECK (frequency IN ('Monthly', 'Quarterly', 'Yearly')),
    item_name         TEXT NOT NULL,
    quantity          REAL NOT NULL,
    price             REAL NOT NULL,
    tax_percent       REAL NOT NULL DEFAULT 0,
    next_invoice_date TEXT NOT NULL,
    is_active         INTEGER NOT NULL DEFAULT 1,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (org_id) REFERENCES organizations(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(id)
);

CREATE INDEX IF NOT EXISTS idx_customers_org ON customers(org_id);
CREATE INDEX IF NOT EXISTS idx_products_org ON products(org_id);
CREATE INDEX IF NOT EXISTS idx_recurring_org ON recurring_rules(org_id);
CREATE INDEX IF NOT EXISTS idx_audit_org ON audit_log(org_id);
CREATE INDEX IF NOT EXISTS idx_users_org ON users(org_id);

CREATE INDEX IF NOT EXISTS idx_customers_user ON customers(user_id);
CREATE INDEX IF NOT EXISTS idx_invoices_customer ON invoices(customer_id);
CREATE INDEX IF NOT EXISTS idx_invoices_status   ON invoices(status);
CREATE INDEX IF NOT EXISTS idx_items_invoice      ON invoice_items(invoice_id);
CREATE INDEX IF NOT EXISTS idx_payments_invoice   ON payments(invoice_id);
CREATE INDEX IF NOT EXISTS idx_products_user       ON products(user_id);
CREATE INDEX IF NOT EXISTS idx_stock_moves_product  ON stock_movements(product_id);
CREATE INDEX IF NOT EXISTS idx_audit_user           ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_notifications_user   ON notifications(user_id, is_read);
CREATE INDEX IF NOT EXISTS idx_recurring_user       ON recurring_rules(user_id);
