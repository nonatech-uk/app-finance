-- PayPal transaction cache and matching tables

CREATE TABLE IF NOT EXISTS paypal_transaction (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    paypal_transaction_id TEXT UNIQUE NOT NULL,
    paypal_order_id TEXT,
    transaction_type TEXT NOT NULL DEFAULT 'payment',
    description TEXT NOT NULL,
    amount NUMERIC(10,2),
    fee NUMERIC(10,2),
    net_amount NUMERIC(10,2),
    currency TEXT NOT NULL DEFAULT 'GBP',
    counterparty TEXT,
    counterparty_email TEXT,
    transaction_date TIMESTAMPTZ,
    status TEXT,
    raw_json JSONB,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_paypal_txn_type ON paypal_transaction(transaction_type);
CREATE INDEX IF NOT EXISTS idx_paypal_txn_date ON paypal_transaction(transaction_date DESC);
CREATE INDEX IF NOT EXISTS idx_paypal_txn_desc ON paypal_transaction USING GIN(to_tsvector('english', description));

CREATE TABLE IF NOT EXISTS paypal_transaction_match (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    paypal_transaction_id UUID NOT NULL REFERENCES paypal_transaction(id) ON DELETE CASCADE,
    raw_transaction_id UUID NOT NULL REFERENCES raw_transaction(id) ON DELETE CASCADE,
    match_confidence NUMERIC(3,2) DEFAULT 1.0,
    matched_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_paypal_match_unique
    ON paypal_transaction_match (paypal_transaction_id, raw_transaction_id);
CREATE INDEX IF NOT EXISTS idx_paypal_match_raw ON paypal_transaction_match(raw_transaction_id);

-- Grant read access to mcp_readonly
GRANT SELECT ON paypal_transaction TO mcp_readonly;
GRANT SELECT ON paypal_transaction_match TO mcp_readonly;
