// ── Auth ──

export interface UserInfo {
  email: string
  display_name: string
  allowed_scopes: string[]
  role: string
}

// ── Transactions ──

export interface TransactionItem {
  id: string
  source: string
  institution: string
  account_ref: string
  posted_at: string
  amount: string
  currency: string
  raw_merchant: string | null
  raw_memo: string | null
  cleaned_merchant: string | null
  canonical_merchant_id: string | null
  canonical_merchant_name: string | null
  merchant_match_type: string | null
  category_path: string | null
  category_name: string | null
  category_type: string | null
  category_is_override: boolean
  is_split: boolean
  note: string | null
  tags: string[]
}

export interface TagItem {
  tag: string
  source: string
}

export interface TransactionList {
  items: TransactionItem[]
  next_cursor: string | null
  has_more: boolean
}

export interface DedupMember {
  raw_transaction_id: string
  source: string
  is_preferred: boolean
}

export interface DedupGroupInfo {
  group_id: string
  match_rule: string
  confidence: string
  members: DedupMember[]
}

export interface EconomicEventLeg {
  raw_transaction_id: string
  leg_type: string
  amount: string
  currency: string
}

export interface EconomicEventInfo {
  event_id: string
  event_type: string
  initiated_at: string | null
  description: string | null
  legs: EconomicEventLeg[]
}

export interface SplitLineItem {
  id: string
  line_number: number
  amount: string
  currency: string
  category_path: string | null
  category_name: string | null
  description: string | null
}

export interface TransactionDetail extends Omit<TransactionItem, 'tags'> {
  raw_data: Record<string, unknown> | null
  note: string | null
  note_source: string | null
  tags: TagItem[]
  dedup_group: DedupGroupInfo | null
  economic_event: EconomicEventInfo | null
  split_lines: SplitLineItem[]
}

// ── Bulk Operations ──

export interface BulkOperationResult {
  ok: boolean
  affected: number
}

export interface BulkMerchantNameResult extends BulkOperationResult {
  merchant_ids: string[]
}

export interface BulkTagReplaceResult extends BulkOperationResult {
  removed: number
}

// ── Accounts ──

export interface AccountItem {
  id: string | null
  institution: string
  account_ref: string
  currency: string
  transaction_count: number
  earliest_date: string | null
  latest_date: string | null
  balance: string | null
  name: string | null
  display_name: string | null
  account_type: string | null
  is_active: boolean | null
  is_archived: boolean
  exclude_from_reports: boolean
  scope: string
  is_favourite: boolean
  display_order: number | null
}

export interface AccountList {
  items: AccountItem[]
}

export interface AccountUpdate {
  display_name?: string | null
  is_archived?: boolean
  exclude_from_reports?: boolean
  scope?: string
  is_favourite?: boolean
  display_order?: number | null
}

export interface AccountDetailResponse {
  institution: string
  account_ref: string
  summary: {
    transaction_count: number
    earliest_date: string
    latest_date: string
    balance: number
    currency: string
    account_name: string | null
    display_name: string | null
    account_type: string | null
    is_active: boolean | null
    is_archived: boolean
    exclude_from_reports: boolean
    scope: string
  }
  recent_transactions: TransactionItem[]
}

// ── Merchants ──

export interface MerchantItem {
  id: string
  name: string
  display_name: string | null
  category_hint: string | null
  category_method: string | null
  category_confidence: string | null
  mapping_count: number
  last_transaction_date: string | null
}

export interface MerchantList {
  items: MerchantItem[]
  next_cursor: string | null
  has_more: boolean
}

export interface MerchantTransaction {
  id: string
  posted_at: string
  amount: string
  currency: string
  raw_merchant: string | null
  source: string
  institution: string
  account_ref: string
}

export interface MerchantDetail {
  id: string
  name: string
  display_name: string | null
  category_hint: string | null
  category_method: string | null
  category_confidence: string | null
  mapping_count: number
  aliases: string[]
  recent_transactions: MerchantTransaction[]
}

export interface DisplayRule {
  id: number
  pattern: string
  display_name: string
  merge_group: boolean
  category_hint: string | null
  priority: number
}

export interface DisplayRuleList {
  items: DisplayRule[]
}

export interface CategorySuggestionItem {
  id: number
  canonical_merchant_id: string
  merchant_name: string
  suggested_category_id: string
  suggested_category_path: string
  method: string
  confidence: string
  reasoning: string | null
  status: string
  created_at: string
}

export interface CategorySuggestionList {
  items: CategorySuggestionItem[]
  total: number
}

// ── Categories ──

export interface CategoryItem {
  id: string
  name: string
  full_path: string
  category_type: string | null
  is_active: boolean
  parent_id: string | null
  children: CategoryItem[]
}

export interface CategoryTree {
  items: CategoryItem[]
}

export interface SpendingByCategory {
  category_path: string
  category_name: string
  category_type: string | null
  total: string
  transaction_count: number
}

export interface SpendingReport {
  items: SpendingByCategory[]
  date_from: string
  date_to: string
  total_income: string
  total_expense: string
}

// ── CSV Import ──

export interface CsvPreviewTransaction {
  transaction_ref: string | null
  posted_at: string | null
  amount: string
  currency: string
  raw_merchant: string | null
}

export interface CsvMismatch {
  transaction_ref: string
  posted_at: string | null
  raw_merchant: string | null
  csv_amount: string
  db_amount: string
}

export interface CsvPreviewResult {
  format: string
  total_rows: number
  new_count: number
  existing_count: number
  mismatch_count: number
  new_transactions: CsvPreviewTransaction[]
  mismatches: CsvMismatch[]
}

export interface CsvImportResult {
  inserted: number
  skipped: number
  pipeline: Record<string, unknown>
}

// ── Stats ──

export interface MonthlyTotal {
  month: string
  income: string
  expense: string
  net: string
  transaction_count: number
}

export interface MonthlyReport {
  items: MonthlyTotal[]
  currency: string
}

export interface AccountOption {
  institution: string
  account_ref: string
  label: string
}

export interface OverviewStats {
  total_accounts: number
  active_accounts: number
  total_raw_transactions: number
  active_transactions: number
  dedup_groups: number
  removed_by_dedup: number
  category_coverage_pct: string
  accounts: AccountOption[]
  date_range_from: string | null
  date_range_to: string | null
}

// ── Stocks ──

export interface StockHoldingItem {
  id: string
  symbol: string
  name: string
  country: string
  currency: string
  scope: string
  is_active: boolean
  notes: string | null
  current_shares: string | null
  average_cost: string | null
  current_price: string | null
  current_value: string | null
  total_cost: string | null
  unrealised_pnl: string | null
  unrealised_pnl_pct: string | null
  price_date: string | null
}

export interface StockHoldingList {
  items: StockHoldingItem[]
}

export interface StockTradeItem {
  id: string
  holding_id: string
  trade_type: string
  trade_date: string
  quantity: string
  price_per_share: string
  total_cost: string
  fees: string
  currency: string
  notes: string | null
  created_at: string
}

export interface PortfolioSummary {
  total_value: string
  total_cost: string
  unrealised_pnl: string
  unrealised_pnl_pct: string
  holdings: StockHoldingItem[]
  price_date: string | null
}

export interface DisposalItem {
  trade_id: string
  holding_id: string
  symbol: string
  trade_date: string
  quantity: string
  proceeds: string
  cost_basis: string
  gain_loss: string
  match_type: string
}

export interface CgtSummary {
  tax_year: string
  disposals: DisposalItem[]
  total_gains: string
  total_losses: string
  net_gains: string
  exempt_amount: string
  taxable_gains: string
  gross_income: string | null
  basic_rate_amount: string
  higher_rate_amount: string
  basic_rate_tax: string
  higher_rate_tax: string
  total_tax: string
}

export interface TaxYearIncomeItem {
  id: string
  tax_year: string
  gross_income: string
  personal_allowance: string
  notes: string | null
}

// ── Other Assets ──

export interface AssetHoldingItem {
  id: string
  name: string
  asset_type: string
  currency: string
  scope: string
  is_active: boolean
  notes: string | null
  latest_gross_value: string | null
  latest_tax_payable: string | null
  latest_net_value: string | null
  valuation_date: string | null
}

export interface AssetValuationItem {
  id: string
  holding_id: string
  valuation_date: string
  gross_value: string
  tax_payable: string
  net_value: string
  notes: string | null
  created_at: string
}

export interface AssetsSummary {
  total_gross_value: string
  total_tax_payable: string
  total_net_value: string
  holdings: AssetHoldingItem[]
}

// ── Tag Rules ──

export interface TagRuleItem {
  id: number
  name: string
  date_from: string | null
  date_to: string | null
  account_ids: string[]
  merchant_pattern: string | null
  category_pattern: string | null
  tags: string[]
  is_active: boolean
  priority: number
  created_at: string
  updated_at: string
}

export interface TagRuleList {
  items: TagRuleItem[]
}

export interface TagRuleCreate {
  name: string
  date_from?: string | null
  date_to?: string | null
  account_ids?: string[]
  merchant_pattern?: string | null
  category_pattern?: string | null
  tags: string[]
  is_active?: boolean
  priority?: number
}

export interface TagRuleUpdate {
  name?: string
  date_from?: string | null
  date_to?: string | null
  account_ids?: string[]
  merchant_pattern?: string | null
  category_pattern?: string | null
  tags?: string[]
  is_active?: boolean
  priority?: number
}

export interface TagRuleApplyResult {
  rules_applied: number
  tags_created: number
  tags_removed: number
}

// ── Tag Management ──

export interface TagSummaryItem {
  tag: string
  count: number
  sources: Record<string, number>
  rule_ids: number[]
}

export interface TagSummaryList {
  items: TagSummaryItem[]
}

// ── Settings ──

export interface SettingsResponse {
  caldav_enabled: boolean
  caldav_tag: string
  caldav_password_set: boolean
  receipt_alert_days: number
  receipt_match_date_tolerance: number
  receipt_auto_match_enabled: boolean
  receipt_amount_tolerance_pct: number
  anthropic_api_key_set: boolean
  webhook_receipt_enabled: boolean
  webhook_receipt_secret: string
  webhook_receipt_allowed_senders: string
}

export interface SettingsUpdate {
  caldav_enabled?: boolean
  caldav_tag?: string
  caldav_password?: string
  receipt_alert_days?: number
  receipt_match_date_tolerance?: number
  receipt_auto_match_enabled?: boolean
  receipt_amount_tolerance_pct?: number
  anthropic_api_key?: string
  webhook_receipt_enabled?: boolean
  webhook_receipt_secret?: string
  webhook_receipt_allowed_senders?: string
}
