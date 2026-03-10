"""Pydantic response models for the Finance API."""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


# ── Auth ──────────────────────────────────────────────────────────────────────


class UserInfo(BaseModel):
    email: str
    display_name: str
    allowed_scopes: list[str]
    role: str


# ── Transactions ──────────────────────────────────────────────────────────────


class TransactionItem(BaseModel):
    """Transaction in list view."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source: str
    institution: str
    account_ref: str
    posted_at: date
    amount: Decimal
    currency: str
    raw_merchant: str | None = None
    raw_memo: str | None = None
    cleaned_merchant: str | None = None
    canonical_merchant_id: UUID | None = None
    canonical_merchant_name: str | None = None
    merchant_match_type: str | None = None
    category_path: str | None = None
    category_name: str | None = None
    category_type: str | None = None
    category_is_override: bool = False
    merchant_is_override: bool = False
    is_split: bool = False
    note: str | None = None
    tags: list[str] = []


class TransactionList(BaseModel):
    items: list[TransactionItem]
    next_cursor: str | None = None
    has_more: bool = False


class DedupMember(BaseModel):
    raw_transaction_id: UUID
    source: str
    is_preferred: bool


class DedupGroupInfo(BaseModel):
    group_id: UUID
    match_rule: str
    confidence: Decimal
    members: list[DedupMember]


class EconomicEventLeg(BaseModel):
    raw_transaction_id: UUID
    leg_type: str
    amount: Decimal
    currency: str


class EconomicEventInfo(BaseModel):
    event_id: UUID
    event_type: str
    initiated_at: date | None = None
    description: str | None = None
    legs: list[EconomicEventLeg]


class TagItem(BaseModel):
    """A tag on a transaction."""

    tag: str
    source: str


class SplitLineItem(BaseModel):
    """A single line within a split transaction."""

    id: UUID
    line_number: int
    amount: Decimal
    currency: str
    category_path: str | None = None
    category_name: str | None = None
    description: str | None = None


class SplitLineInput(BaseModel):
    """Input for a single split line."""

    amount: Decimal
    category_path: str | None = None
    description: str | None = None


class SplitRequest(BaseModel):
    """Request body for creating/replacing a split."""

    lines: list[SplitLineInput]


class TransactionDetail(TransactionItem):
    """Full transaction detail including dedup and economic event info."""

    raw_data: dict | None = None
    note: str | None = None
    note_source: str | None = None
    tags: list[TagItem] = []  # type: ignore[assignment]
    dedup_group: DedupGroupInfo | None = None
    economic_event: EconomicEventInfo | None = None
    split_lines: list[SplitLineItem] = []


class NoteUpdate(BaseModel):
    """Request body for updating a transaction note."""

    note: str


class CategoryUpdate(BaseModel):
    """Request body for updating a transaction category override."""

    category_path: str


class TagUpdate(BaseModel):
    """Request body for adding a tag."""

    tag: str


class LinkTransferRequest(BaseModel):
    """Request body for linking two transactions as a transfer."""

    counterpart_id: UUID


# ── Bulk Operations ──────────────────────────────────────────────────────────


class BulkCategoryUpdate(BaseModel):
    """Bulk set category override on multiple transactions."""

    transaction_ids: list[UUID]
    category_path: str  # empty = remove override


class BulkMerchantNameUpdate(BaseModel):
    """Bulk update display_name for all canonical merchants of given transactions."""

    transaction_ids: list[UUID]
    display_name: str | None = None


class BulkTagAdd(BaseModel):
    """Add tag(s) to multiple transactions."""

    transaction_ids: list[UUID]
    tags: list[str]


class BulkTagRemove(BaseModel):
    """Remove a single tag from multiple transactions."""

    transaction_ids: list[UUID]
    tag: str


class BulkTagReplace(BaseModel):
    """Replace all tags on selected transactions with a new set."""

    transaction_ids: list[UUID]
    tags: list[str]  # empty = delete all


class BulkNoteUpdate(BaseModel):
    """Bulk set or append notes on multiple transactions."""

    transaction_ids: list[UUID]
    note: str
    mode: str = "replace"  # "replace" or "append"


# ── Accounts ──────────────────────────────────────────────────────────────────


class AccountItem(BaseModel):
    id: UUID | None = None
    institution: str
    account_ref: str
    currency: str
    name: str | None = None
    display_name: str | None = None
    account_type: str | None = None
    is_active: bool = True
    is_archived: bool = False
    exclude_from_reports: bool = False
    scope: str = "personal"
    is_favourite: bool = False
    display_order: int | None = None
    transaction_count: int = 0
    earliest_date: date | None = None
    latest_date: date | None = None
    balance: Decimal | None = None


class AccountList(BaseModel):
    items: list[AccountItem]


class AccountUpdate(BaseModel):
    """Request body for updating account metadata."""

    display_name: str | None = None
    is_archived: bool | None = None
    exclude_from_reports: bool | None = None
    scope: str | None = None
    is_favourite: bool | None = None
    display_order: int | None = None


# ── Merchants ─────────────────────────────────────────────────────────────────


class MerchantItem(BaseModel):
    id: UUID
    name: str
    display_name: str | None = None
    category_hint: str | None = None
    category_method: str | None = None
    category_confidence: Decimal | None = None
    mapping_count: int = 0
    last_transaction_date: date | None = None


class MerchantList(BaseModel):
    items: list[MerchantItem]
    next_cursor: str | None = None
    has_more: bool = False


class MerchantTransaction(BaseModel):
    id: UUID
    posted_at: date
    amount: Decimal
    currency: str
    raw_merchant: str | None = None
    source: str
    institution: str
    account_ref: str


class MerchantDetail(BaseModel):
    id: UUID
    name: str
    display_name: str | None = None
    category_hint: str | None = None
    category_method: str | None = None
    category_confidence: Decimal | None = None
    mapping_count: int = 0
    aliases: list[str] = []
    recent_transactions: list[MerchantTransaction] = []


class MerchantMappingUpdate(BaseModel):
    """Request body for updating a merchant's category hint."""

    category_hint: str | None = None


class MerchantNameUpdate(BaseModel):
    """Request body for updating a merchant's display name."""

    display_name: str | None = None


class MerchantMergeRequest(BaseModel):
    """Request body to merge another merchant into this one."""

    merge_from_id: UUID


class BulkMerchantMerge(BaseModel):
    """Merge multiple merchants into one, optionally setting a display name."""

    merchant_ids: list[UUID]
    display_name: str | None = None


class AliasSplitRequest(BaseModel):
    """Split a single alias off into its own merchant."""

    alias: str


class CategorySuggestionItem(BaseModel):
    id: int
    canonical_merchant_id: UUID
    merchant_name: str
    suggested_category_id: UUID
    suggested_category_path: str
    method: str
    confidence: Decimal
    reasoning: str | None = None
    status: str
    created_at: datetime


class CategorySuggestionList(BaseModel):
    items: list[CategorySuggestionItem]
    total: int = 0


class SuggestionReview(BaseModel):
    """Request body for accepting or rejecting a suggestion."""

    status: str  # 'accepted' or 'rejected'


class DisplayRuleItem(BaseModel):
    id: int
    pattern: str
    display_name: str
    merge_group: bool = True
    category_hint: str | None = None
    priority: int = 100


class DisplayRuleList(BaseModel):
    items: list[DisplayRuleItem]


class DisplayRuleCreate(BaseModel):
    pattern: str
    display_name: str
    merge_group: bool = True
    category_hint: str | None = None
    priority: int = 100


class SplitRuleItem(BaseModel):
    id: int
    merchant_pattern: str
    amount_exact: Decimal | None = None
    amount_min: Decimal | None = None
    amount_max: Decimal | None = None
    target_merchant_id: UUID
    target_merchant_name: str | None = None
    priority: int = 100
    description: str | None = None


class SplitRuleList(BaseModel):
    items: list[SplitRuleItem]


class SplitRuleCreate(BaseModel):
    merchant_pattern: str
    amount_exact: Decimal | None = None
    amount_min: Decimal | None = None
    amount_max: Decimal | None = None
    target_merchant_id: UUID
    priority: int = 100
    description: str | None = None


# ── Categories ────────────────────────────────────────────────────────────────


class CategoryItem(BaseModel):
    id: UUID
    name: str
    full_path: str
    category_type: str | None = None
    is_active: bool = True
    parent_id: UUID | None = None
    children: list["CategoryItem"] = []


class CategoryTree(BaseModel):
    items: list[CategoryItem]


class CategoryRename(BaseModel):
    new_name: str


class CategoryCreate(BaseModel):
    name: str
    parent_id: UUID | None = None
    category_type: str  # 'income' or 'expense'


class CategoryDelete(BaseModel):
    reassign_to: UUID


class SpendingByCategory(BaseModel):
    category_path: str
    category_name: str
    category_type: str | None = None
    total: Decimal
    transaction_count: int


class SpendingReport(BaseModel):
    items: list[SpendingByCategory]
    date_from: date
    date_to: date
    total_income: Decimal
    total_expense: Decimal


# ── CSV Import ────────────────────────────────────────────────────────────────


class CsvPreviewTransaction(BaseModel):
    transaction_ref: str | None = None
    posted_at: str | None = None
    amount: str
    currency: str = "GBP"
    raw_merchant: str | None = None


class CsvMismatch(BaseModel):
    transaction_ref: str
    posted_at: str | None = None
    raw_merchant: str | None = None
    csv_amount: str
    db_amount: str


class CsvPreviewResult(BaseModel):
    format: str
    total_rows: int
    new_count: int
    existing_count: int
    mismatch_count: int
    new_transactions: list[CsvPreviewTransaction] = []
    mismatches: list[CsvMismatch] = []


class CsvConfirmRequest(BaseModel):
    institution: str
    account_ref: str
    format: str


class CsvImportResult(BaseModel):
    inserted: int
    skipped: int
    pipeline: dict = {}


# ── Bankivity Import ─────────────────────────────────────────────────────────


class BankivityPreviewRequest(BaseModel):
    bank8_path: str


class BankivityPreviewResult(BaseModel):
    total: int
    new_count: int
    existing_count: int
    by_account: dict[str, int]
    path: str


class BankivityImportResult(BaseModel):
    inserted: int
    skipped: int
    pipeline: dict = {}


# ── Stats ─────────────────────────────────────────────────────────────────────


class MonthlyTotal(BaseModel):
    month: str  # YYYY-MM
    income: Decimal
    expense: Decimal
    net: Decimal
    transaction_count: int


class MonthlyReport(BaseModel):
    items: list[MonthlyTotal]
    currency: str


class AccountOption(BaseModel):
    institution: str
    account_ref: str
    label: str


class OverviewStats(BaseModel):
    total_accounts: int
    active_accounts: int
    total_raw_transactions: int
    active_transactions: int
    dedup_groups: int
    removed_by_dedup: int
    category_coverage_pct: Decimal
    accounts: list[AccountOption]
    date_range_from: date | None = None
    date_range_to: date | None = None


# ── Stocks ───────────────────────────────────────────────────────────────────


class StockHoldingItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    symbol: str
    name: str
    country: str
    currency: str
    scope: str
    is_active: bool
    notes: str | None = None
    current_shares: Decimal | None = None
    average_cost: Decimal | None = None
    current_price: Decimal | None = None
    current_value: Decimal | None = None
    total_cost: Decimal | None = None
    unrealised_pnl: Decimal | None = None
    unrealised_pnl_pct: Decimal | None = None
    price_date: date | None = None


class StockHoldingCreate(BaseModel):
    symbol: str
    name: str
    country: str = "US"
    currency: str = "USD"
    scope: str = "personal"
    notes: str | None = None


class StockHoldingUpdate(BaseModel):
    name: str | None = None
    country: str | None = None
    is_active: bool | None = None
    notes: str | None = None


class StockTradeItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    holding_id: UUID
    trade_type: str
    trade_date: date
    quantity: Decimal
    price_per_share: Decimal
    total_cost: Decimal
    fees: Decimal
    currency: str
    notes: str | None = None
    created_at: datetime


class StockTradeCreate(BaseModel):
    trade_type: str
    trade_date: date
    quantity: Decimal
    price_per_share: Decimal
    fees: Decimal = Decimal("0")
    notes: str | None = None


class PortfolioSummary(BaseModel):
    total_value: Decimal
    total_cost: Decimal
    unrealised_pnl: Decimal
    unrealised_pnl_pct: Decimal
    holdings: list[StockHoldingItem]
    price_date: date | None = None


class DisposalItem(BaseModel):
    trade_id: UUID
    holding_id: UUID
    symbol: str
    trade_date: date
    quantity: Decimal
    proceeds: Decimal
    cost_basis: Decimal
    gain_loss: Decimal
    match_type: str


class CgtSummary(BaseModel):
    tax_year: str
    disposals: list[DisposalItem]
    total_gains: Decimal
    total_losses: Decimal
    net_gains: Decimal
    exempt_amount: Decimal
    taxable_gains: Decimal
    gross_income: Decimal | None = None
    basic_rate_amount: Decimal
    higher_rate_amount: Decimal
    basic_rate_tax: Decimal
    higher_rate_tax: Decimal
    total_tax: Decimal


class TaxYearIncomeItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tax_year: str
    gross_income: Decimal
    personal_allowance: Decimal
    notes: str | None = None


class TaxYearIncomeUpdate(BaseModel):
    gross_income: Decimal
    personal_allowance: Decimal = Decimal("12570")
    notes: str | None = None


# ── Other Assets ─────────────────────────────────────────────────────────────


class AssetHoldingItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    asset_type: str
    currency: str
    scope: str
    is_active: bool
    notes: str | None = None
    latest_gross_value: Decimal | None = None
    latest_tax_payable: Decimal | None = None
    latest_net_value: Decimal | None = None
    valuation_date: date | None = None


class AssetHoldingCreate(BaseModel):
    name: str
    asset_type: str = "other"
    currency: str = "GBP"
    scope: str = "personal"
    notes: str | None = None


class AssetHoldingUpdate(BaseModel):
    name: str | None = None
    asset_type: str | None = None
    is_active: bool | None = None
    notes: str | None = None


class AssetValuationItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    holding_id: UUID
    valuation_date: date
    gross_value: Decimal
    tax_payable: Decimal
    net_value: Decimal
    notes: str | None = None
    created_at: datetime


class AssetValuationCreate(BaseModel):
    valuation_date: date
    gross_value: Decimal
    tax_payable: Decimal = Decimal("0")
    notes: str | None = None


class AssetsSummary(BaseModel):
    total_gross_value: Decimal
    total_tax_payable: Decimal
    total_net_value: Decimal
    holdings: list[AssetHoldingItem]


# ── Tag Rules ────────────────────────────────────────────────────────────────


class TagRuleItem(BaseModel):
    id: int
    name: str
    date_from: date | None = None
    date_to: date | None = None
    account_ids: list[UUID] = []
    merchant_pattern: str | None = None
    category_pattern: str | None = None
    tags: list[str] = []
    is_active: bool = True
    priority: int = 100
    created_at: datetime
    updated_at: datetime


class TagRuleList(BaseModel):
    items: list[TagRuleItem]


class TagRuleCreate(BaseModel):
    name: str
    date_from: date | None = None
    date_to: date | None = None
    account_ids: list[UUID] = []
    merchant_pattern: str | None = None
    category_pattern: str | None = None
    tags: list[str]
    is_active: bool = True
    priority: int = 100


class TagRuleUpdate(BaseModel):
    name: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    account_ids: list[UUID] | None = None
    merchant_pattern: str | None = None
    category_pattern: str | None = None
    tags: list[str] | None = None
    is_active: bool | None = None
    priority: int | None = None


class TagRuleApplyResult(BaseModel):
    rules_applied: int
    tags_created: int
    tags_removed: int


class TagSummaryItem(BaseModel):
    tag: str
    count: int
    sources: dict[str, int]
    rule_ids: list[int]


class TagSummaryList(BaseModel):
    items: list[TagSummaryItem]


class TagRename(BaseModel):
    new_name: str


# ── Settings ─────────────────────────────────────────────────────────────────


class SettingsResponse(BaseModel):
    caldav_enabled: bool
    caldav_tag: str
    caldav_password_set: bool  # never expose actual password
    # Receipt settings
    receipt_alert_days: int = 7
    receipt_match_date_tolerance: int = 7
    receipt_auto_match_enabled: bool = True
    receipt_amount_tolerance_pct: int = 20
    anthropic_api_key_set: bool = False
    # Email webhook settings
    webhook_receipt_enabled: bool = False
    webhook_receipt_secret: str = ""
    webhook_receipt_allowed_senders: str = ""
    # Bankivity import
    bankivity_last_path: str = ""


class SettingsUpdate(BaseModel):
    caldav_enabled: bool | None = None
    caldav_tag: str | None = None
    caldav_password: str | None = None  # empty string = disable auth
    # Receipt settings
    receipt_alert_days: int | None = None
    receipt_match_date_tolerance: int | None = None
    receipt_auto_match_enabled: bool | None = None
    receipt_amount_tolerance_pct: int | None = None
    anthropic_api_key: str | None = None
    # Email webhook settings
    webhook_receipt_enabled: bool | None = None
    webhook_receipt_secret: str | None = None
    webhook_receipt_allowed_senders: str | None = None


# ── Receipts ────────────────────────────────────────────────────────────────


class ReceiptItem(BaseModel):
    id: UUID
    original_filename: str
    mime_type: str
    file_size: int

    ocr_status: str
    extracted_date: date | None = None
    extracted_amount: Decimal | None = None
    extracted_currency: str | None = None
    extracted_merchant: str | None = None

    match_status: str
    matched_transaction_id: UUID | None = None
    match_confidence: Decimal | None = None
    matched_at: datetime | None = None
    matched_by: str | None = None

    source: str
    uploaded_at: datetime
    uploaded_by: str | None = None
    notes: str | None = None


class ReceiptDetail(ReceiptItem):
    ocr_text: str | None = None
    ocr_data: dict | None = None
    file_path: str | None = None
    thumbnail_path: str | None = None
    matched_transaction: dict | None = None  # summary of linked txn


class ReceiptList(BaseModel):
    items: list[ReceiptItem]
    total: int = 0


class ReceiptUpdate(BaseModel):
    extracted_date: date | None = None
    extracted_amount: Decimal | None = None
    extracted_currency: str | None = None
    extracted_merchant: str | None = None


class ReceiptMatchRequest(BaseModel):
    transaction_id: UUID


class ReceiptCandidate(BaseModel):
    id: UUID
    posted_at: date
    amount: Decimal
    currency: str | None = None
    raw_merchant: str | None = None
    institution: str
    account_ref: str


# ── Cash Accounts ───────────────────────────────────────────────────────────


class CashTransactionCreate(BaseModel):
    account_ref: str       # e.g. "cash_gbp"
    posted_at: date
    amount: Decimal        # negative = spending, positive = income/deposit
    description: str
    category_path: str | None = None
    tags: list[str] = []
    note: str | None = None


class CashBalanceReset(BaseModel):
    target_balance: Decimal
    posted_at: date
