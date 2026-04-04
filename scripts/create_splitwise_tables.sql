-- Splitwise sync tracking (mirrors xero_sync_log pattern)
CREATE TABLE IF NOT EXISTS splitwise_sync_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    raw_transaction_id UUID REFERENCES raw_transaction(id),
    splitwise_expense_id BIGINT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('pull', 'push')),
    dismissed BOOLEAN NOT NULL DEFAULT false,
    permanent BOOLEAN NOT NULL DEFAULT false,
    synced_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (splitwise_expense_id)
);

-- Partial unique index: only enforce uniqueness for non-NULL transaction links
CREATE UNIQUE INDEX IF NOT EXISTS uq_splitwise_sync_txn
    ON splitwise_sync_log(raw_transaction_id) WHERE raw_transaction_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_splitwise_sync_expense
    ON splitwise_sync_log(splitwise_expense_id);
CREATE INDEX IF NOT EXISTS idx_splitwise_sync_txn
    ON splitwise_sync_log(raw_transaction_id);
