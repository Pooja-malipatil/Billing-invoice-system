-- ============================================================
-- Billing & Invoice Management System - Database Schema
-- ============================================================

PRAGMA foreign_keys = ON;

-- 1) CUSTOMERS -------------------------------------------------
CREATE TABLE IF NOT EXISTS customers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    email       TEXT,
    phone       TEXT,
    address     TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2) INVOICES ----------------------------------------------------
-- One customer can have many invoices  -> customers (1) --- (many) invoices
CREATE TABLE IF NOT EXISTS invoices (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id   INTEGER NOT NULL,
    invoice_date  TEXT NOT NULL,          -- 'YYYY-MM-DD'
    due_date      TEXT NOT NULL,          -- 'YYYY-MM-DD'
    tax_percent   REAL NOT NULL DEFAULT 0,
    subtotal      REAL NOT NULL DEFAULT 0, -- sum(quantity*price) of line items
    tax_amount    REAL NOT NULL DEFAULT 0, -- subtotal * tax_percent / 100
    total         REAL NOT NULL DEFAULT 0, -- subtotal + tax_amount
    status        TEXT NOT NULL DEFAULT 'Pending'
                  CHECK (status IN ('Paid', 'Pending', 'Overdue')),
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (customer_id) REFERENCES customers(id)
    -- NOTE: no ON DELETE CASCADE here on purpose - see README
    -- "Design decisions" section for why.
);

-- 3) INVOICE_ITEMS ----------------------------------------------
-- One invoice can have many line items -> invoices (1) --- (many) invoice_items
CREATE TABLE IF NOT EXISTS invoice_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id   INTEGER NOT NULL,
    item_name    TEXT NOT NULL,
    quantity     REAL NOT NULL,
    price        REAL NOT NULL,
    FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
    -- CASCADE here is fine: deleting an invoice should always wipe its
    -- own line items, that's not losing independent business data.
);

-- Indexes to keep filtered/joined queries fast as data grows
CREATE INDEX IF NOT EXISTS idx_invoices_customer ON invoices(customer_id);
CREATE INDEX IF NOT EXISTS idx_invoices_status   ON invoices(status);
CREATE INDEX IF NOT EXISTS idx_items_invoice      ON invoice_items(invoice_id);
