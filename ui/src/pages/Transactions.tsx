import { useState, useMemo, useCallback, useEffect, useRef } from 'react'
import { useTransactions, useTransaction, useUpdateNote, useUpdateTransactionCategory, useLinkTransfer, useUnlinkEvent, useAllTags, useAddTag, useRemoveTag, useBulkUpdateCategory, useBulkUpdateMerchantName, useBulkAddTags, useBulkRemoveTag, useBulkReplaceTags, useBulkUpdateNote, useSaveSplit, useDeleteSplit, useSuggestAmazonSplit } from '../hooks/useTransactions'
import { useUpdateMerchantName } from '../hooks/useMerchants'
import { useCategories } from '../hooks/useCategories'
import { useOverview } from '../hooks/useStats'
import { useScope } from '../contexts/ScopeContext'
import CurrencyAmount from '../components/common/CurrencyAmount'
import Badge from '../components/common/Badge'
import LoadingSpinner from '../components/common/LoadingSpinner'
import JsonViewer from '../components/common/JsonViewer'
import type { TransactionItem, TransactionDetail, CategoryItem, TagItem } from '../api/types'
import type { SplitLineInput } from '../api/transactions'
import { useTransactionReceipts, useUploadReceipt } from '../hooks/useReceipts'
import { receiptFileUrl, receiptThumbnailUrl } from '../api/receipts'
import Lightbox from '../components/common/Lightbox'

function SortableHeader({
  label, sortKey, currentSort, currentDir, onSort, align = 'left',
}: {
  label: string
  sortKey: string
  currentSort: string
  currentDir: 'asc' | 'desc'
  onSort: (key: string) => void
  align?: 'left' | 'right'
}) {
  const isActive = currentSort === sortKey
  const arrow = isActive ? (currentDir === 'asc' ? ' ▲' : ' ▼') : ''
  return (
    <th
      className={`pb-2 pr-4 cursor-pointer hover:text-accent select-none ${align === 'right' ? 'text-right' : ''}`}
      onClick={() => onSort(sortKey)}
    >
      {label}{arrow}
    </th>
  )
}

export default function Transactions() {
  const [search, setSearch] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [account, setAccount] = useState('')  // "institution/account_ref" or ""
  const [currency, setCurrency] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [amountMin, setAmountMin] = useState('')
  const [amountMax, setAmountMax] = useState('')
  const [tagFilter, setTagFilter] = useState('')
  const [debouncedTagFilter, setDebouncedTagFilter] = useState('')
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [selectMode, setSelectMode] = useState(false)
  const [uncategorised, setUncategorised] = useState(false)
  const [sortBy, setSortBy] = useState('posted_at')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  const { scope } = useScope()
  const { data: overview } = useOverview(scope)

  // Debounce search + tag filter
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 300)
    return () => clearTimeout(t)
  }, [search])
  useEffect(() => {
    const t = setTimeout(() => setDebouncedTagFilter(tagFilter), 300)
    return () => clearTimeout(t)
  }, [tagFilter])

  const [filterInstitution, filterAccountRef] = useMemo(() => {
    if (!account) return [undefined, undefined]
    const [inst, ...rest] = account.split('/')
    return [inst, rest.join('/')]
  }, [account])

  const handleSort = (key: string) => {
    if (sortBy === key) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortBy(key)
      setSortDir(key === 'posted_at' ? 'desc' : 'asc')
    }
  }

  const filters = useMemo(() => ({
    limit: 100,
    search: debouncedSearch || undefined,
    institution: filterInstitution,
    account_ref: filterAccountRef,
    currency: currency || undefined,
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
    amount_min: amountMin ? Number(amountMin) : undefined,
    amount_max: amountMax ? Number(amountMax) : undefined,
    tag_pattern: debouncedTagFilter || undefined,
    uncategorised: uncategorised || undefined,
    sort_by: sortBy,
    sort_dir: sortDir,
    scope,
  }), [debouncedSearch, filterInstitution, filterAccountRef, currency, dateFrom, dateTo, amountMin, amountMax, debouncedTagFilter, uncategorised, sortBy, sortDir, scope])

  const { data, isLoading, fetchNextPage, hasNextPage, isFetchingNextPage } = useTransactions(filters)
  const { data: detail, isLoading: detailLoading } = useTransaction(selectedId)

  const allItems = useMemo(() => {
    if (!data) return []
    return data.pages.flatMap(p => p.items)
  }, [data])

  const accountLabelMap = useMemo(() => {
    const map: Record<string, string> = {}
    if (overview?.accounts) {
      for (const a of overview.accounts) {
        map[`${a.institution}/${a.account_ref}`] = a.label
      }
    }
    return map
  }, [overview])

  const clearFilters = useCallback(() => {
    setSearch('')
    setAccount('')
    setCurrency('')
    setDateFrom('')
    setDateTo('')
    setAmountMin('')
    setAmountMax('')
    setTagFilter('')
    setUncategorised(false)
  }, [])

  const selectionCount = selectedIds.size

  const toggleSelection = useCallback((id: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const selectAll = useCallback(() => {
    setSelectedIds(new Set(allItems.map(t => t.id)))
  }, [allItems])

  const deselectAll = useCallback(() => {
    setSelectedIds(new Set())
  }, [])

  const enterSelectMode = useCallback(() => {
    setSelectMode(true)
    setSelectedId(null)
  }, [])

  const exitSelectMode = useCallback(() => {
    setSelectMode(false)
    setSelectedIds(new Set())
  }, [])

  const selectedItems = useMemo(() => {
    return allItems.filter(t => selectedIds.has(t.id))
  }, [allItems, selectedIds])

  return (
    <div className="flex gap-0 h-[calc(100vh-3rem)]">
      {/* Main list */}
      <div className={`flex-1 flex flex-col min-w-0 ${selectedId ? 'mr-[480px]' : ''}`}>
        <h2 className="text-xl font-semibold mb-4">Transactions</h2>

        {/* Filter bar */}
        <div className="flex flex-wrap gap-3 mb-4">
          <input
            type="text"
            placeholder="Search merchants, notes, amounts..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="bg-bg-card border border-border rounded-md px-3 py-1.5 text-sm text-text-primary placeholder:text-text-secondary focus:outline-none focus:border-accent w-56"
          />
          <select
            value={account}
            onChange={e => setAccount(e.target.value)}
            className="bg-bg-card border border-border rounded-md px-3 py-1.5 text-sm text-text-primary focus:outline-none focus:border-accent"
          >
            <option value="">All accounts</option>
            {overview?.accounts.map(a => (
              <option key={`${a.institution}/${a.account_ref}`} value={`${a.institution}/${a.account_ref}`}>{a.label}</option>
            ))}
          </select>
          <select
            value={currency}
            onChange={e => setCurrency(e.target.value)}
            className="bg-bg-card border border-border rounded-md px-3 py-1.5 text-sm text-text-primary focus:outline-none focus:border-accent"
          >
            <option value="">All currencies</option>
            {['GBP', 'CHF', 'EUR', 'USD', 'PLN', 'NOK'].map(c => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
          <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} className="bg-bg-card border border-border rounded-md px-3 py-1.5 text-sm text-text-primary focus:outline-none focus:border-accent" />
          <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} className="bg-bg-card border border-border rounded-md px-3 py-1.5 text-sm text-text-primary focus:outline-none focus:border-accent" />
          <div className="flex items-center gap-1">
            <input
              type="number"
              placeholder="Min £"
              value={amountMin}
              onChange={e => setAmountMin(e.target.value)}
              className="bg-bg-card border border-border rounded-md px-2 py-1.5 text-sm text-text-primary focus:outline-none focus:border-accent w-20"
            />
            <span className="text-text-secondary text-xs">to</span>
            <input
              type="number"
              placeholder="Max £"
              value={amountMax}
              onChange={e => setAmountMax(e.target.value)}
              className="bg-bg-card border border-border rounded-md px-2 py-1.5 text-sm text-text-primary focus:outline-none focus:border-accent w-20"
            />
            {[
              { label: 'Debits', min: '', max: '-0.01' },
              { label: 'Credits', min: '0.01', max: '' },
            ].map(p => (
              <button
                key={p.label}
                onClick={() => { setAmountMin(p.min); setAmountMax(p.max) }}
                className={`px-2 py-1 text-xs rounded border transition-colors ${
                  amountMin === p.min && amountMax === p.max
                    ? 'bg-accent/20 border-accent text-accent'
                    : 'border-border text-text-secondary hover:border-accent hover:text-accent'
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>
          <input
            type="text"
            placeholder="Tag filter (regex)"
            value={tagFilter}
            onChange={e => setTagFilter(e.target.value)}
            className="bg-bg-card border border-border rounded-md px-2 py-1.5 text-sm text-text-primary focus:outline-none focus:border-accent w-36 font-mono"
          />
          <label className="inline-flex items-center gap-1.5 text-sm text-text-secondary cursor-pointer select-none">
            <input
              type="checkbox"
              checked={uncategorised}
              onChange={e => setUncategorised(e.target.checked)}
              className="accent-accent"
            />
            Uncategorised
          </label>
          <button onClick={clearFilters} className="text-text-secondary hover:text-text-primary text-sm px-2">Clear</button>
          {!selectMode ? (
            <button onClick={enterSelectMode} className="text-text-secondary hover:text-accent text-sm px-2 ml-auto">Select</button>
          ) : (
            <button onClick={exitSelectMode} className="text-accent hover:text-accent-hover text-sm px-2 ml-auto">Exit Select</button>
          )}
        </div>

        {/* Table */}
        {isLoading ? <LoadingSpinner /> : (
          <div className="flex-1 overflow-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-bg-primary">
                <tr className="text-text-secondary text-left text-xs uppercase tracking-wider">
                  {selectMode && (
                    <th className="pb-2 pr-2 pl-1 w-8">
                      <input
                        type="checkbox"
                        checked={selectionCount > 0 && selectionCount === allItems.length}
                        ref={el => { if (el) el.indeterminate = selectionCount > 0 && selectionCount < allItems.length }}
                        onChange={() => selectionCount === allItems.length ? deselectAll() : selectAll()}
                        className="accent-accent"
                      />
                    </th>
                  )}
                  <SortableHeader label="Date" sortKey="posted_at" currentSort={sortBy} currentDir={sortDir} onSort={handleSort} />
                  <SortableHeader label="Merchant" sortKey="merchant" currentSort={sortBy} currentDir={sortDir} onSort={handleSort} />
                  <SortableHeader label="Category" sortKey="category" currentSort={sortBy} currentDir={sortDir} onSort={handleSort} />
                  <th className="pb-2 pr-4 text-left">Tags</th>
                  <SortableHeader label="Amount" sortKey="amount" currentSort={sortBy} currentDir={sortDir} onSort={handleSort} align="right" />
                  <SortableHeader label="Account" sortKey="source" currentSort={sortBy} currentDir={sortDir} onSort={handleSort} />
                </tr>
              </thead>
              <tbody>
                {allItems.map(txn => (
                  <TransactionRow
                    key={txn.id}
                    txn={txn}
                    isSelected={txn.id === selectedId}
                    isChecked={selectedIds.has(txn.id)}
                    selectMode={selectMode}
                    accountLabel={accountLabelMap[`${txn.institution}/${txn.account_ref}`] || txn.account_ref}
                    onClick={() => setSelectedId(txn.id === selectedId ? null : txn.id)}
                    onToggle={() => toggleSelection(txn.id)}
                  />
                ))}
              </tbody>
            </table>
            {hasNextPage && (
              <div className="py-4 text-center">
                <button
                  onClick={() => fetchNextPage()}
                  disabled={isFetchingNextPage}
                  className="text-accent hover:text-accent-hover text-sm px-4 py-2 border border-accent/30 rounded-md hover:bg-accent/10 disabled:opacity-50"
                >
                  {isFetchingNextPage ? 'Loading...' : `Load more (${allItems.length} shown)`}
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Bulk edit toolbar */}
      {selectMode && selectionCount > 0 && (
        <BulkEditToolbar
          selectedIds={selectedIds}
          selectedItems={selectedItems}
          onClose={exitSelectMode}
        />
      )}

      {/* Detail panel */}
      {selectedId && !selectMode && (
        <div className="fixed right-0 top-0 h-full w-[480px] bg-bg-secondary border-l border-border overflow-auto p-5 z-10">
          <div className="flex justify-between items-center mb-4">
            <h3 className="text-lg font-semibold">Transaction Detail</h3>
            <button onClick={() => setSelectedId(null)} className="text-text-secondary hover:text-text-primary text-xl">&times;</button>
          </div>
          {detailLoading ? <LoadingSpinner /> : detail && <TransactionDetailContent detail={detail} />}
        </div>
      )}
    </div>
  )
}

function TransactionRow({ txn, isSelected, isChecked, selectMode, accountLabel, onClick, onToggle }: {
  txn: TransactionItem; isSelected: boolean; isChecked: boolean; selectMode: boolean; accountLabel: string
  onClick: () => void; onToggle: () => void
}) {
  const handleClick = () => {
    if (selectMode) onToggle()
    else onClick()
  }

  return (
    <tr
      onClick={handleClick}
      className={`border-b border-border/50 cursor-pointer transition-colors ${
        isChecked ? 'bg-accent/15' : isSelected ? 'bg-accent/10' : 'hover:bg-bg-hover'
      }`}
    >
      {selectMode && (
        <td className="py-2 pr-2 pl-1 w-8">
          <input
            type="checkbox"
            checked={isChecked}
            onChange={onToggle}
            onClick={e => e.stopPropagation()}
            className="accent-accent"
          />
        </td>
      )}
      <td className="py-2 pr-4 whitespace-nowrap text-text-secondary">{txn.posted_at}</td>
      <td className="py-2 pr-4">
        <div className="truncate max-w-[300px] flex items-center gap-1.5">
          <span className="truncate">{txn.canonical_merchant_name || txn.cleaned_merchant || txn.raw_merchant || '—'}</span>
          {txn.note && (
            <span className="flex-shrink-0" title={txn.note}>
              <svg className="w-3.5 h-3.5 text-text-secondary opacity-50" viewBox="0 0 16 16" fill="currentColor">
                <path d="M2 3.5A1.5 1.5 0 013.5 2h9A1.5 1.5 0 0114 3.5v9a1.5 1.5 0 01-1.5 1.5h-9A1.5 1.5 0 012 12.5v-9zM4 5h8v1H4V5zm0 2.5h8v1H4v-1zM4 10h5v1H4v-1z"/>
              </svg>
            </span>
          )}
          {txn.is_split && (
            <span className="flex-shrink-0" title="Split transaction">
              <svg className="w-3.5 h-3.5 text-accent opacity-70" viewBox="0 0 16 16" fill="currentColor">
                <path d="M8 1v4l3 3-3 3v4M8 1v4L5 8l3 3v4" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </span>
          )}
        </div>
      </td>
      <td className="py-2 pr-4">
        {txn.is_split ? (
          <Badge variant="default">Split</Badge>
        ) : txn.category_path ? (
          <Badge variant="accent">{txn.category_path}</Badge>
        ) : (
          <span className="text-text-secondary text-xs">—</span>
        )}
      </td>
      <td className="py-2 pr-4">
        {txn.tags.length > 0 ? (
          <div className="flex flex-wrap gap-1">
            {txn.tags.map(t => (
              <span key={t} className="px-1.5 py-0.5 text-xs rounded bg-accent/15 text-accent">{t}</span>
            ))}
          </div>
        ) : null}
      </td>
      <td className="py-2 pr-4 text-right">
        <CurrencyAmount amount={txn.amount} currency={txn.currency} showSign={false} />
      </td>
      <td className="py-2 pr-4">
        <Badge>{accountLabel}</Badge>
      </td>
    </tr>
  )
}

export function TransactionDetailContent({ detail }: { detail: import('../api/types').TransactionDetail }) {
  return (
    <div className="space-y-5 text-sm">
      {/* Basic info */}
      <section>
        <h4 className="text-xs uppercase text-text-secondary mb-2">Basic Info</h4>
        <div className="grid grid-cols-2 gap-2">
          <div><span className="text-text-secondary">Date:</span> {detail.posted_at}</div>
          <div><span className="text-text-secondary">Amount:</span> <CurrencyAmount amount={detail.amount} currency={detail.currency} /></div>
          <div><span className="text-text-secondary">Institution:</span> {detail.institution}</div>
          <div><span className="text-text-secondary">Account:</span> {detail.account_ref}</div>
          <div><span className="text-text-secondary">Source:</span> <Badge>{detail.source}</Badge></div>
          <div><span className="text-text-secondary">Currency:</span> {detail.currency}</div>
        </div>
      </section>

      {/* Merchant + Category */}
      <MerchantSection detail={detail} />

      {/* Split */}
      <SplitSection detail={detail} />

      {detail.raw_memo && (
        <section>
          <h4 className="text-xs uppercase text-text-secondary mb-2">Memo</h4>
          <div>{detail.raw_memo}</div>
        </section>
      )}

      {/* Note */}
      <NoteSection transactionId={detail.id} note={detail.note} noteSource={detail.note_source} />

      {/* Tags */}
      <TagSection transactionId={detail.id} tags={detail.tags} />

      {/* Receipts */}
      <ReceiptSection transactionId={detail.id} />

      {/* Dedup group */}
      {detail.dedup_group && (
        <section>
          <h4 className="text-xs uppercase text-text-secondary mb-2">Dedup Group</h4>
          <div className="mb-2 text-text-secondary text-xs">
            Rule: {detail.dedup_group.match_rule} · Confidence: {detail.dedup_group.confidence}
          </div>
          <div className="space-y-1">
            {detail.dedup_group.members.map(m => (
              <div key={m.raw_transaction_id} className="flex items-center gap-2">
                <Badge variant={m.is_preferred ? 'income' : 'default'}>{m.is_preferred ? 'preferred' : 'duplicate'}</Badge>
                <span className="text-text-secondary">{m.source}</span>
                <span className="text-text-secondary text-xs truncate">{m.raw_transaction_id}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Economic event / Transfer linking */}
      <TransferSection detail={detail} />

      {/* Raw data */}
      {detail.raw_data && (
        <section>
          <h4 className="text-xs uppercase text-text-secondary mb-2">Raw Data</h4>
          <div className="bg-bg-primary rounded p-3 text-xs font-mono overflow-auto max-h-64">
            <JsonViewer data={detail.raw_data} />
          </div>
        </section>
      )}
    </div>
  )
}

function flattenCategories(items: CategoryItem[], prefix = ''): { path: string; name: string }[] {
  const result: { path: string; name: string }[] = []
  for (const cat of items) {
    result.push({ path: cat.full_path, name: prefix ? `${prefix} > ${cat.name}` : cat.name })
    if (cat.children.length > 0) {
      result.push(...flattenCategories(cat.children, cat.full_path))
    }
  }
  return result
}

function MerchantSection({ detail }: { detail: TransactionDetail }) {
  const [displayName, setDisplayName] = useState(detail.canonical_merchant_name || '')
  const [editingName, setEditingName] = useState(false)
  const nameMutation = useUpdateMerchantName()
  const categoryMutation = useUpdateTransactionCategory()
  const { data: categoryTree } = useCategories()

  const categoryOptions = useMemo(() => {
    if (!categoryTree) return []
    return flattenCategories(categoryTree.items)
  }, [categoryTree])

  // Reset display name when detail changes
  useEffect(() => {
    if (!editingName) setDisplayName(detail.canonical_merchant_name || '')
  }, [detail.canonical_merchant_id, editingName])

  const handleNameSave = () => {
    if (!detail.canonical_merchant_id) return
    nameMutation.mutate(
      { id: detail.canonical_merchant_id, displayName: displayName.trim() || null },
      { onSuccess: () => setEditingName(false) },
    )
  }

  const handleCategoryChange = (path: string) => {
    categoryMutation.mutate({ id: detail.id, categoryPath: path })
  }

  return (
    <section>
      <h4 className="text-xs uppercase text-text-secondary mb-2">Merchant</h4>
      <div className="space-y-3">
        {/* Display name (editable if canonical merchant exists) */}
        {detail.canonical_merchant_id ? (
          <div>
            <label className="text-text-secondary text-xs block mb-1">Display Name</label>
            {editingName ? (
              <div className="flex gap-2">
                <input
                  type="text"
                  value={displayName}
                  onChange={e => setDisplayName(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') handleNameSave(); if (e.key === 'Escape') { setDisplayName(detail.canonical_merchant_name || ''); setEditingName(false) } }}
                  placeholder="Set a display name..."
                  className="flex-1 bg-bg-primary border border-border rounded px-2 py-1 text-sm text-text-primary focus:outline-none focus:border-accent"
                  autoFocus
                />
                <button
                  onClick={handleNameSave}
                  disabled={nameMutation.isPending}
                  className="px-3 py-1 text-xs bg-accent/20 text-accent rounded hover:bg-accent/30 disabled:opacity-50"
                >
                  {nameMutation.isPending ? '...' : 'Save'}
                </button>
                <button
                  onClick={() => { setDisplayName(detail.canonical_merchant_name || ''); setEditingName(false) }}
                  className="text-xs text-text-secondary hover:text-text-primary"
                >
                  Cancel
                </button>
              </div>
            ) : (
              <div className="flex items-center gap-2">
                <span>{detail.canonical_merchant_name || '—'}</span>
                <button
                  onClick={() => setEditingName(true)}
                  className="text-text-secondary hover:text-accent text-xs ml-auto"
                >
                  Edit
                </button>
              </div>
            )}
          </div>
        ) : (
          <div className="text-text-secondary text-xs italic">No merchant linked</div>
        )}

        {/* Category (transaction-level override) */}
        <div>
          <label className="text-text-secondary text-xs block mb-1">
            Category
            {detail.category_is_override && (
              <span className="ml-1.5 text-[10px] bg-accent/20 text-accent px-1.5 py-0.5 rounded">override</span>
            )}
          </label>
          <select
            value={detail.category_path || ''}
            onChange={e => handleCategoryChange(e.target.value)}
            disabled={categoryMutation.isPending}
            className="w-full bg-bg-primary border border-border rounded px-2 py-1 text-sm text-text-primary focus:outline-none focus:border-accent disabled:opacity-50"
          >
            <option value="">-- None --</option>
            {categoryOptions.map(opt => (
              <option key={opt.path} value={opt.path}>{opt.path}</option>
            ))}
          </select>
        </div>

        {/* Raw details */}
        <div className="text-xs text-text-secondary space-y-0.5 pt-1 border-t border-border/50">
          <div>Raw: {detail.raw_merchant || '—'}</div>
          <div>Cleaned: {detail.cleaned_merchant || '—'}</div>
          <div>Match: {detail.merchant_match_type || '—'}</div>
        </div>
      </div>
    </section>
  )
}

function TransferSection({ detail }: { detail: TransactionDetail }) {
  const [linking, setLinking] = useState(false)
  const [counterpartId, setCounterpartId] = useState('')
  const linkMutation = useLinkTransfer()
  const unlinkMutation = useUnlinkEvent()

  const handleLink = () => {
    const id = counterpartId.trim()
    if (!id) return
    linkMutation.mutate(
      { id: detail.id, counterpartId: id },
      { onSuccess: () => { setLinking(false); setCounterpartId('') } },
    )
  }

  const handleUnlink = () => {
    if (!detail.economic_event) return
    unlinkMutation.mutate({ eventId: detail.economic_event.event_id })
  }

  if (detail.economic_event) {
    return (
      <section>
        <div className="flex items-center gap-2 mb-2">
          <h4 className="text-xs uppercase text-text-secondary">Transfer</h4>
          <button
            onClick={handleUnlink}
            disabled={unlinkMutation.isPending}
            className="text-text-secondary hover:text-red-400 text-xs ml-auto disabled:opacity-50"
          >
            {unlinkMutation.isPending ? '...' : 'Unlink'}
          </button>
        </div>
        <div className="mb-2">
          <Badge variant="accent">{detail.economic_event.event_type.replace(/_/g, ' ')}</Badge>
          {detail.economic_event.description && (
            <span className="ml-2 text-text-secondary text-xs">{detail.economic_event.description}</span>
          )}
        </div>
        <div className="space-y-1">
          {detail.economic_event.legs.map((leg, i) => (
            <div key={i} className="flex gap-3 text-text-secondary">
              <Badge>{leg.leg_type}</Badge>
              <CurrencyAmount amount={leg.amount} currency={leg.currency} />
            </div>
          ))}
        </div>
      </section>
    )
  }

  return (
    <section>
      <div className="flex items-center gap-2 mb-2">
        <h4 className="text-xs uppercase text-text-secondary">Transfer</h4>
        {!linking && (
          <button
            onClick={() => setLinking(true)}
            className="text-text-secondary hover:text-accent text-xs ml-auto"
          >
            + Link as transfer
          </button>
        )}
      </div>
      {linking ? (
        <div className="space-y-2">
          <input
            type="text"
            value={counterpartId}
            onChange={e => setCounterpartId(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') handleLink(); if (e.key === 'Escape') { setLinking(false); setCounterpartId('') } }}
            placeholder="Counterpart transaction ID..."
            className="w-full bg-bg-primary border border-border rounded-md px-3 py-1.5 text-sm text-text-primary placeholder:text-text-secondary focus:outline-none focus:border-accent font-mono"
            autoFocus
          />
          {linkMutation.isError && (
            <div className="text-red-400 text-xs">{(linkMutation.error as Error).message || 'Failed to link'}</div>
          )}
          <div className="flex gap-2">
            <button
              onClick={handleLink}
              disabled={linkMutation.isPending || !counterpartId.trim()}
              className="text-xs px-3 py-1 bg-accent text-white rounded-md hover:bg-accent-hover disabled:opacity-50"
            >
              {linkMutation.isPending ? 'Linking...' : 'Link'}
            </button>
            <button
              onClick={() => { setLinking(false); setCounterpartId('') }}
              className="text-xs px-3 py-1 text-text-secondary hover:text-text-primary"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="text-text-secondary text-xs italic">No linked transfer</div>
      )}
    </section>
  )
}

// ── Split Transaction ────────────────────────────────────────────────────────

function SplitSection({ detail }: { detail: TransactionDetail }) {
  const [editing, setEditing] = useState(false)
  const [lines, setLines] = useState<{ amount: string; category_path: string; description: string }[]>([])
  const saveMutation = useSaveSplit()
  const deleteMutation = useDeleteSplit()
  const amazonQuery = useSuggestAmazonSplit(detail.id)
  const { data: categoryTree } = useCategories()

  const categoryOptions = useMemo(() => {
    if (!categoryTree) return []
    return flattenCategories(categoryTree.items)
  }, [categoryTree])

  const parentAmount = useMemo(() => parseFloat(detail.amount), [detail.amount])

  const lineSum = useMemo(
    () => lines.reduce((s, l) => s + (parseFloat(l.amount) || 0), 0),
    [lines],
  )
  const remaining = useMemo(() => {
    const r = parentAmount - lineSum
    return Math.round(r * 100) / 100
  }, [parentAmount, lineSum])

  const startEdit = useCallback(() => {
    if (detail.split_lines.length > 0) {
      setLines(detail.split_lines.map(sl => ({
        amount: sl.amount,
        category_path: sl.category_path || '',
        description: sl.description || '',
      })))
    } else {
      setLines([
        { amount: '', category_path: '', description: '' },
        { amount: '', category_path: '', description: '' },
      ])
    }
    setEditing(true)
  }, [detail.split_lines])

  const handleSave = () => {
    const splitLines: SplitLineInput[] = lines.map(l => ({
      amount: parseFloat(l.amount),
      category_path: l.category_path || null,
      description: l.description || null,
    }))
    saveMutation.mutate({ id: detail.id, lines: splitLines }, {
      onSuccess: () => setEditing(false),
    })
  }

  const handleUnsplit = () => {
    deleteMutation.mutate({ id: detail.id }, {
      onSuccess: () => setEditing(false),
    })
  }

  const handleFillAmazon = () => {
    amazonQuery.refetch().then(result => {
      if (result.data) {
        setLines(result.data.lines.map(l => ({
          amount: l.amount,
          category_path: l.category_path || '',
          description: l.description || '',
        })))
      }
    })
  }

  const updateLine = (idx: number, field: string, value: string) => {
    setLines(prev => prev.map((l, i) => i === idx ? { ...l, [field]: value } : l))
  }

  const removeLine = (idx: number) => {
    setLines(prev => prev.filter((_, i) => i !== idx))
  }

  const addLine = () => {
    setLines(prev => [...prev, { amount: '', category_path: '', description: '' }])
  }

  const autoFillLast = () => {
    if (lines.length === 0) return
    setLines(prev => prev.map((l, i) =>
      i === prev.length - 1 ? { ...l, amount: remaining.toFixed(4) } : l
    ))
  }

  // Viewing existing split (not editing)
  if (detail.split_lines.length > 0 && !editing) {
    return (
      <section>
        <div className="flex items-center gap-2 mb-2">
          <h4 className="text-xs uppercase text-text-secondary">Split ({detail.split_lines.length} lines)</h4>
          <button onClick={startEdit} className="text-text-secondary hover:text-accent text-xs ml-auto">Edit</button>
          <button
            onClick={handleUnsplit}
            disabled={deleteMutation.isPending}
            className="text-text-secondary hover:text-red-400 text-xs disabled:opacity-50"
          >
            {deleteMutation.isPending ? '...' : 'Unsplit'}
          </button>
        </div>
        <div className="space-y-1">
          {detail.split_lines.map(sl => (
            <div key={sl.id} className="flex items-center gap-2 text-xs bg-bg-card rounded px-2 py-1.5">
              <span className="flex-1 truncate text-text-primary">{sl.description || '—'}</span>
              {sl.category_path ? (
                <Badge variant="accent">{sl.category_path}</Badge>
              ) : (
                <span className="text-text-secondary">—</span>
              )}
              <span className="font-mono text-right w-20">
                <CurrencyAmount amount={sl.amount} currency={sl.currency} showSign={false} />
              </span>
            </div>
          ))}
        </div>
      </section>
    )
  }

  // Editor mode
  if (editing) {
    const canSave = lines.length >= 2 && Math.abs(remaining) < 0.005 && lines.every(l => l.amount !== '')

    return (
      <section>
        <h4 className="text-xs uppercase text-text-secondary mb-2">Split Transaction</h4>
        <div className="space-y-2">
          {lines.map((line, idx) => (
            <div key={idx} className="flex gap-1.5 items-start">
              <input
                type="number"
                step="0.01"
                value={line.amount}
                onChange={e => updateLine(idx, 'amount', e.target.value)}
                placeholder="Amount"
                className="w-24 bg-bg-primary border border-border rounded px-2 py-1 text-xs text-text-primary focus:outline-none focus:border-accent font-mono"
              />
              <select
                value={line.category_path}
                onChange={e => updateLine(idx, 'category_path', e.target.value)}
                className="flex-1 bg-bg-primary border border-border rounded px-1.5 py-1 text-xs text-text-primary focus:outline-none focus:border-accent min-w-0"
              >
                <option value="">-- Category --</option>
                {categoryOptions.map(opt => (
                  <option key={opt.path} value={opt.path}>{opt.path}</option>
                ))}
              </select>
              <input
                type="text"
                value={line.description}
                onChange={e => updateLine(idx, 'description', e.target.value)}
                placeholder="Description"
                className="flex-1 bg-bg-primary border border-border rounded px-2 py-1 text-xs text-text-primary focus:outline-none focus:border-accent min-w-0"
              />
              {lines.length > 2 && (
                <button onClick={() => removeLine(idx)} className="text-text-secondary hover:text-red-400 text-xs px-1">×</button>
              )}
            </div>
          ))}

          <div className="flex items-center gap-2 pt-1">
            <button onClick={addLine} className="text-accent hover:text-accent-hover text-xs">+ Add line</button>
            <button onClick={autoFillLast} className="text-text-secondary hover:text-accent text-xs">Auto-fill last</button>
            <button onClick={handleFillAmazon} disabled={amazonQuery.isFetching} className="text-text-secondary hover:text-accent text-xs disabled:opacity-50">
              {amazonQuery.isFetching ? '...' : 'Fill from Amazon'}
            </button>
            <span className={`ml-auto text-xs font-mono ${Math.abs(remaining) < 0.005 ? 'text-green-400' : 'text-red-400'}`}>
              Remaining: {remaining >= 0 ? '' : '−'}{detail.currency} {Math.abs(remaining).toFixed(2)}
            </span>
          </div>

          {saveMutation.isError && (
            <div className="text-red-400 text-xs">{(saveMutation.error as Error).message || 'Failed to save split'}</div>
          )}

          <div className="flex gap-2 pt-1">
            <button
              onClick={handleSave}
              disabled={!canSave || saveMutation.isPending}
              className="text-xs px-3 py-1 bg-accent text-white rounded-md hover:bg-accent-hover disabled:opacity-50"
            >
              {saveMutation.isPending ? 'Saving...' : 'Save Split'}
            </button>
            <button onClick={() => setEditing(false)} className="text-xs px-3 py-1 text-text-secondary hover:text-text-primary">
              Cancel
            </button>
          </div>
        </div>
      </section>
    )
  }

  // Not split — show button
  return (
    <section>
      <div className="flex items-center gap-2 mb-2">
        <h4 className="text-xs uppercase text-text-secondary">Split</h4>
        <button onClick={startEdit} className="text-text-secondary hover:text-accent text-xs ml-auto">
          Split this transaction
        </button>
      </div>
      <div className="text-text-secondary text-xs italic">Not split</div>
    </section>
  )
}

// ── Bulk Edit Components ─────────────────────────────────────────────────────

function BulkEditToolbar({ selectedIds, selectedItems, onClose }: {
  selectedIds: Set<string>; selectedItems: TransactionItem[]; onClose: () => void
}) {
  const [action, setAction] = useState<'category' | 'name' | 'note' | 'tag-add' | 'tag-remove' | 'tag-replace' | null>(null)
  const ids = useMemo(() => Array.from(selectedIds), [selectedIds])
  const count = ids.length

  const commonTags = useMemo(() => {
    if (!selectedItems.length) return []
    const first = new Set(selectedItems[0].tags)
    for (const item of selectedItems.slice(1)) {
      const itemTags = new Set(item.tags)
      for (const tag of first) {
        if (!itemTags.has(tag)) first.delete(tag)
      }
    }
    return Array.from(first).sort()
  }, [selectedItems])

  const distinctMerchantCount = useMemo(() => {
    const mIds = new Set<string>()
    for (const item of selectedItems) {
      if (item.canonical_merchant_id) mIds.add(item.canonical_merchant_id)
    }
    return mIds.size
  }, [selectedItems])

  const btnClass = "text-xs px-3 py-1.5 rounded-md border border-border hover:bg-bg-hover text-text-primary"
  const activeBtnClass = "text-xs px-3 py-1.5 rounded-md bg-accent text-white"

  return (
    <div className="fixed bottom-0 left-0 right-0 bg-bg-secondary border-t border-border shadow-lg z-20">
      <div className="flex items-center gap-3 px-4 py-2.5">
        <span className="text-sm font-medium text-accent">{count} selected</span>
        <div className="w-px h-5 bg-border" />

        <button onClick={() => setAction(action === 'category' ? null : 'category')} className={action === 'category' ? activeBtnClass : btnClass}>
          Set Category
        </button>
        <button onClick={() => setAction(action === 'name' ? null : 'name')} className={action === 'name' ? activeBtnClass : btnClass}>
          Set Name {distinctMerchantCount > 1 && <span className="text-warning ml-1">({distinctMerchantCount})</span>}
        </button>
        <button onClick={() => setAction(action === 'tag-add' ? null : 'tag-add')} className={action === 'tag-add' ? activeBtnClass : btnClass}>
          Add Tag
        </button>
        {commonTags.length > 0 && (
          <button onClick={() => setAction(action === 'tag-remove' ? null : 'tag-remove')} className={action === 'tag-remove' ? activeBtnClass : btnClass}>
            Remove Tag
          </button>
        )}
        <button onClick={() => setAction(action === 'tag-replace' ? null : 'tag-replace')} className={action === 'tag-replace' ? activeBtnClass : btnClass}>
          Replace Tags
        </button>
        <div className="w-px h-5 bg-border" />
        <button onClick={() => setAction(action === 'note' ? null : 'note')} className={action === 'note' ? activeBtnClass : btnClass}>
          Set Note
        </button>

        <button onClick={onClose} className="ml-auto text-text-secondary hover:text-text-primary text-sm">✕ Cancel</button>
      </div>

      {action && (
        <div className="border-t border-border px-4 py-3">
          {action === 'category' && <BulkCategoryPanel ids={ids} onDone={() => setAction(null)} />}
          {action === 'name' && <BulkNamePanel ids={ids} merchantCount={distinctMerchantCount} onDone={() => setAction(null)} />}
          {action === 'tag-add' && <BulkTagAddPanel ids={ids} onDone={() => setAction(null)} />}
          {action === 'tag-remove' && <BulkTagRemovePanel ids={ids} commonTags={commonTags} onDone={() => setAction(null)} />}
          {action === 'tag-replace' && <BulkTagReplacePanel ids={ids} onDone={() => setAction(null)} />}
          {action === 'note' && <BulkNotePanel ids={ids} onDone={() => setAction(null)} />}
        </div>
      )}
    </div>
  )
}

function BulkCategoryPanel({ ids, onDone }: { ids: string[]; onDone: () => void }) {
  const [categoryPath, setCategoryPath] = useState('')
  const mutation = useBulkUpdateCategory()
  const { data: categoryTree } = useCategories()

  const categoryOptions = useMemo(() => {
    if (!categoryTree) return []
    return flattenCategories(categoryTree.items)
  }, [categoryTree])

  const handleApply = () => {
    mutation.mutate({ ids, categoryPath }, { onSuccess: onDone })
  }

  return (
    <div className="flex items-center gap-3">
      <label className="text-sm text-text-secondary">Category:</label>
      <select
        value={categoryPath}
        onChange={e => setCategoryPath(e.target.value)}
        className="bg-bg-primary border border-border rounded-md px-2 py-1 text-sm text-text-primary focus:outline-none focus:border-accent min-w-[200px]"
      >
        <option value="">-- Remove override --</option>
        {categoryOptions.map(opt => (
          <option key={opt.path} value={opt.path}>{opt.path}</option>
        ))}
      </select>
      <button
        onClick={handleApply}
        disabled={mutation.isPending}
        className="text-xs px-3 py-1 bg-accent text-white rounded-md hover:bg-accent-hover disabled:opacity-50"
      >
        {mutation.isPending ? 'Applying...' : `Apply to ${ids.length}`}
      </button>
    </div>
  )
}

function BulkNamePanel({ ids, merchantCount, onDone }: { ids: string[]; merchantCount: number; onDone: () => void }) {
  const [displayName, setDisplayName] = useState('')
  const mutation = useBulkUpdateMerchantName()

  const handleApply = () => {
    mutation.mutate({ ids, displayName: displayName.trim() || null }, { onSuccess: onDone })
  }

  return (
    <div className="flex items-center gap-3">
      <label className="text-sm text-text-secondary">Display Name:</label>
      <input
        type="text"
        value={displayName}
        onChange={e => setDisplayName(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter') handleApply() }}
        placeholder="New display name..."
        className="bg-bg-primary border border-border rounded-md px-2 py-1 text-sm text-text-primary focus:outline-none focus:border-accent min-w-[200px]"
        autoFocus
      />
      {merchantCount > 1 && (
        <span className="text-xs text-warning">⚠ Updates {merchantCount} different merchants</span>
      )}
      <button
        onClick={handleApply}
        disabled={mutation.isPending}
        className="text-xs px-3 py-1 bg-accent text-white rounded-md hover:bg-accent-hover disabled:opacity-50"
      >
        {mutation.isPending ? 'Applying...' : 'Apply'}
      </button>
    </div>
  )
}

function BulkTagAddPanel({ ids, onDone }: { ids: string[]; onDone: () => void }) {
  const [input, setInput] = useState('')
  const [highlightIdx, setHighlightIdx] = useState(-1)
  const { data: allTags } = useAllTags()
  const mutation = useBulkAddTags()

  const suggestions = useMemo(() => {
    if (!allTags?.items || !input.trim()) return []
    const q = input.trim().toLowerCase()
    return allTags.items.filter(t => t.tag.toLowerCase().includes(q)).slice(0, 8)
  }, [allTags, input])

  useEffect(() => { setHighlightIdx(-1) }, [suggestions])

  const handleAdd = (tagName: string) => {
    const trimmed = tagName.trim()
    if (!trimmed) return
    mutation.mutate({ ids, tags: [trimmed] }, { onSuccess: () => { setInput(''); onDone() } })
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      if (highlightIdx >= 0 && highlightIdx < suggestions.length) handleAdd(suggestions[highlightIdx].tag)
      else handleAdd(input)
    }
    if (e.key === 'ArrowDown') { e.preventDefault(); setHighlightIdx(prev => Math.min(prev + 1, suggestions.length - 1)) }
    if (e.key === 'ArrowUp') { e.preventDefault(); setHighlightIdx(prev => Math.max(prev - 1, -1)) }
  }

  return (
    <div className="flex items-center gap-3">
      <label className="text-sm text-text-secondary">Add tag:</label>
      <div className="relative">
        <input
          type="text"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Tag name..."
          className="bg-bg-primary border border-border rounded-md px-2 py-1 text-sm text-text-primary focus:outline-none focus:border-accent min-w-[200px]"
          autoFocus
        />
        {suggestions.length > 0 && (
          <div className="absolute z-30 bottom-full left-0 right-0 mb-1 bg-bg-secondary border border-border rounded-md shadow-lg max-h-48 overflow-auto">
            {suggestions.map((s, idx) => (
              <button
                key={s.tag}
                onMouseDown={e => { e.preventDefault(); handleAdd(s.tag) }}
                className={`w-full text-left px-3 py-1.5 text-sm flex justify-between items-center ${
                  idx === highlightIdx ? 'bg-accent/10 text-accent' : 'text-text-primary hover:bg-bg-hover'
                }`}
              >
                <span>{s.tag}</span>
                <span className="text-text-secondary text-xs">{s.count}</span>
              </button>
            ))}
          </div>
        )}
      </div>
      <button
        onClick={() => handleAdd(input)}
        disabled={mutation.isPending || !input.trim()}
        className="text-xs px-3 py-1 bg-accent text-white rounded-md hover:bg-accent-hover disabled:opacity-50"
      >
        {mutation.isPending ? 'Adding...' : `Add to ${ids.length}`}
      </button>
    </div>
  )
}

function BulkTagRemovePanel({ ids, commonTags, onDone }: { ids: string[]; commonTags: string[]; onDone: () => void }) {
  const mutation = useBulkRemoveTag()

  const handleRemove = (tag: string) => {
    mutation.mutate({ ids, tag }, { onSuccess: onDone })
  }

  return (
    <div className="flex items-center gap-3">
      <label className="text-sm text-text-secondary">Remove common tag:</label>
      <div className="flex flex-wrap gap-1.5">
        {commonTags.map(tag => (
          <button
            key={tag}
            onClick={() => handleRemove(tag)}
            disabled={mutation.isPending}
            className="inline-flex items-center gap-1 bg-accent/10 text-accent text-xs px-2 py-0.5 rounded-full hover:bg-red-100 hover:text-red-600 disabled:opacity-50"
          >
            {tag} ×
          </button>
        ))}
      </div>
    </div>
  )
}

function BulkTagReplacePanel({ ids, onDone }: { ids: string[]; onDone: () => void }) {
  const [input, setInput] = useState('')
  const [tags, setTags] = useState<string[]>([])
  const [highlightIdx, setHighlightIdx] = useState(-1)
  const { data: allTags } = useAllTags()
  const mutation = useBulkReplaceTags()

  const suggestions = useMemo(() => {
    if (!allTags?.items || !input.trim()) return []
    const q = input.trim().toLowerCase()
    const existing = new Set(tags)
    return allTags.items.filter(t => !existing.has(t.tag) && t.tag.toLowerCase().includes(q)).slice(0, 8)
  }, [allTags, input, tags])

  useEffect(() => { setHighlightIdx(-1) }, [suggestions])

  const addTag = (tagName: string) => {
    const trimmed = tagName.trim()
    if (trimmed && !tags.includes(trimmed)) {
      setTags(prev => [...prev, trimmed])
    }
    setInput('')
  }

  const removeTag = (tagName: string) => {
    setTags(prev => prev.filter(t => t !== tagName))
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      if (highlightIdx >= 0 && highlightIdx < suggestions.length) addTag(suggestions[highlightIdx].tag)
      else addTag(input)
    }
    if (e.key === 'ArrowDown') { e.preventDefault(); setHighlightIdx(prev => Math.min(prev + 1, suggestions.length - 1)) }
    if (e.key === 'ArrowUp') { e.preventDefault(); setHighlightIdx(prev => Math.max(prev - 1, -1)) }
  }

  const handleReplace = () => {
    mutation.mutate({ ids, tags }, { onSuccess: onDone })
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3">
        <label className="text-sm text-text-secondary">Replace with:</label>
        <div className="relative">
          <input
            type="text"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Add tags to the set..."
            className="bg-bg-primary border border-border rounded-md px-2 py-1 text-sm text-text-primary focus:outline-none focus:border-accent min-w-[200px]"
            autoFocus
          />
          {suggestions.length > 0 && (
            <div className="absolute z-30 bottom-full left-0 right-0 mb-1 bg-bg-secondary border border-border rounded-md shadow-lg max-h-48 overflow-auto">
              {suggestions.map((s, idx) => (
                <button
                  key={s.tag}
                  onMouseDown={e => { e.preventDefault(); addTag(s.tag) }}
                  className={`w-full text-left px-3 py-1.5 text-sm flex justify-between items-center ${
                    idx === highlightIdx ? 'bg-accent/10 text-accent' : 'text-text-primary hover:bg-bg-hover'
                  }`}
                >
                  <span>{s.tag}</span>
                  <span className="text-text-secondary text-xs">{s.count}</span>
                </button>
              ))}
            </div>
          )}
        </div>
        <button
          onClick={handleReplace}
          disabled={mutation.isPending}
          className="text-xs px-3 py-1 bg-accent text-white rounded-md hover:bg-accent-hover disabled:opacity-50"
        >
          {mutation.isPending ? 'Replacing...' : tags.length === 0 ? `Delete all tags (${ids.length})` : `Replace (${ids.length})`}
        </button>
      </div>
      {tags.length > 0 && (
        <div className="flex flex-wrap gap-1.5 ml-[100px]">
          {tags.map(tag => (
            <span key={tag} className="inline-flex items-center gap-1 bg-accent/10 text-accent text-xs px-2 py-0.5 rounded-full">
              {tag}
              <button onClick={() => removeTag(tag)} className="text-accent/60 hover:text-accent ml-0.5">×</button>
            </span>
          ))}
        </div>
      )}
      {tags.length === 0 && (
        <div className="text-xs text-warning ml-[100px]">⚠ This will remove all tags from {ids.length} transactions</div>
      )}
    </div>
  )
}

function BulkNotePanel({ ids, onDone }: { ids: string[]; onDone: () => void }) {
  const [note, setNote] = useState('')
  const [mode, setMode] = useState<'replace' | 'append'>('replace')
  const mutation = useBulkUpdateNote()

  const handleApply = () => {
    mutation.mutate({ ids, note, mode }, { onSuccess: onDone })
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3">
        <label className="text-sm text-text-secondary">Note:</label>
        <div className="flex items-center gap-2">
          <label className="inline-flex items-center gap-1 text-xs text-text-secondary cursor-pointer">
            <input type="radio" name="noteMode" value="replace" checked={mode === 'replace'} onChange={() => setMode('replace')} className="accent-accent" />
            Replace
          </label>
          <label className="inline-flex items-center gap-1 text-xs text-text-secondary cursor-pointer">
            <input type="radio" name="noteMode" value="append" checked={mode === 'append'} onChange={() => setMode('append')} className="accent-accent" />
            Append
          </label>
        </div>
      </div>
      <div className="flex items-start gap-3">
        <textarea
          value={note}
          onChange={e => setNote(e.target.value)}
          placeholder={mode === 'replace' ? 'New note (empty to clear)...' : 'Text to append...'}
          rows={2}
          className="bg-bg-primary border border-border rounded-md px-2 py-1 text-sm text-text-primary focus:outline-none focus:border-accent min-w-[300px] resize-y"
          autoFocus
        />
        <button
          onClick={handleApply}
          disabled={mutation.isPending || (mode === 'append' && !note.trim())}
          className="text-xs px-3 py-1 bg-accent text-white rounded-md hover:bg-accent-hover disabled:opacity-50"
        >
          {mutation.isPending ? 'Applying...' : mode === 'replace' && !note.trim() ? `Clear notes (${ids.length})` : `${mode === 'append' ? 'Append to' : 'Set on'} ${ids.length}`}
        </button>
      </div>
      {mode === 'replace' && !note.trim() && (
        <div className="text-xs text-warning">⚠ This will remove all notes from {ids.length} transactions</div>
      )}
      {mode === 'append' && (
        <div className="text-xs text-text-secondary">Text will be appended on a new line to existing notes</div>
      )}
    </div>
  )
}

function TagSection({ transactionId, tags }: { transactionId: string; tags: TagItem[] }) {
  const [adding, setAdding] = useState(false)
  const [input, setInput] = useState('')
  const [highlightIdx, setHighlightIdx] = useState(-1)
  const inputRef = useRef<HTMLInputElement>(null)
  const dropdownRef = useRef<HTMLDivElement>(null)
  const { data: allTags } = useAllTags()
  const addTag = useAddTag()
  const removeTag = useRemoveTag()

  // Filtered autocomplete suggestions (exclude already-applied tags)
  const existingTagNames = useMemo(() => new Set(tags.map(t => t.tag)), [tags])
  const suggestions = useMemo(() => {
    if (!allTags?.items || !input.trim()) return []
    const q = input.trim().toLowerCase()
    return allTags.items
      .filter(t => !existingTagNames.has(t.tag) && t.tag.toLowerCase().includes(q))
      .slice(0, 8)
  }, [allTags, input, existingTagNames])

  // Reset highlight when suggestions change
  useEffect(() => { setHighlightIdx(-1) }, [suggestions])

  // Focus input when entering add mode
  useEffect(() => {
    if (adding && inputRef.current) inputRef.current.focus()
  }, [adding])

  const handleAdd = (tagName: string) => {
    const trimmed = tagName.trim()
    if (!trimmed) return
    addTag.mutate(
      { id: transactionId, tag: trimmed },
      { onSuccess: () => { setInput(''); setAdding(false) } },
    )
  }

  const handleRemove = (tagName: string) => {
    removeTag.mutate({ id: transactionId, tag: tagName })
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      if (highlightIdx >= 0 && highlightIdx < suggestions.length) {
        handleAdd(suggestions[highlightIdx].tag)
      } else {
        handleAdd(input)
      }
    }
    if (e.key === 'Escape') {
      setInput('')
      setAdding(false)
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHighlightIdx(prev => (prev < suggestions.length - 1 ? prev + 1 : prev))
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHighlightIdx(prev => (prev > 0 ? prev - 1 : -1))
    }
  }

  return (
    <section>
      <div className="flex items-center gap-2 mb-2">
        <h4 className="text-xs uppercase text-text-secondary">Tags</h4>
        {!adding && (
          <button
            onClick={() => setAdding(true)}
            className="text-text-secondary hover:text-accent text-xs ml-auto"
          >
            + Add tag
          </button>
        )}
      </div>

      {/* Existing tags */}
      {tags.length > 0 ? (
        <div className="flex flex-wrap gap-1.5 mb-2">
          {tags.map(t => (
            <span
              key={t.tag}
              className="inline-flex items-center gap-1 bg-accent/10 text-accent text-xs px-2 py-0.5 rounded-full"
            >
              {t.tag}
              {t.source === 'ibank_import' && (
                <span className="text-[9px] text-text-secondary bg-bg-primary px-1 py-px rounded">iBank</span>
              )}
              <button
                onClick={() => handleRemove(t.tag)}
                disabled={removeTag.isPending}
                className="text-accent/60 hover:text-accent ml-0.5 disabled:opacity-50"
                title="Remove tag"
              >
                ×
              </button>
            </span>
          ))}
        </div>
      ) : !adding ? (
        <div className="text-text-secondary text-xs italic mb-2">No tags</div>
      ) : null}

      {/* Add tag input with autocomplete */}
      {adding && (
        <div>
          <div className="relative">
            <input
              ref={inputRef}
              type="text"
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Type a tag name..."
              className="w-full bg-bg-primary border border-border rounded-md px-3 py-1.5 text-sm text-text-primary placeholder:text-text-secondary focus:outline-none focus:border-accent"
            />
            {suggestions.length > 0 && (
              <div
                ref={dropdownRef}
                className="absolute z-20 top-full left-0 right-0 mt-1 bg-bg-secondary border border-border rounded-md shadow-lg max-h-48 overflow-auto"
              >
                {suggestions.map((s, idx) => (
                  <button
                    key={s.tag}
                    onMouseDown={e => { e.preventDefault(); handleAdd(s.tag) }}
                    className={`w-full text-left px-3 py-1.5 text-sm flex justify-between items-center ${
                      idx === highlightIdx ? 'bg-accent/10 text-accent' : 'text-text-primary hover:bg-bg-hover'
                    }`}
                  >
                    <span>{s.tag}</span>
                    <span className="text-text-secondary text-xs">{s.count}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="flex gap-2 mt-2">
            <button
              onClick={() => handleAdd(input)}
              disabled={addTag.isPending || !input.trim()}
              className="text-xs px-3 py-1 bg-accent text-white rounded-md hover:bg-accent-hover disabled:opacity-50"
            >
              {addTag.isPending ? 'Adding...' : 'Add'}
            </button>
            <button
              onClick={() => { setInput(''); setAdding(false) }}
              className="text-xs px-3 py-1 text-text-secondary hover:text-text-primary"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </section>
  )
}

function NoteSection({ transactionId, note, noteSource }: { transactionId: string; note: string | null; noteSource: string | null }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(note || '')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const updateNote = useUpdateNote()

  // Reset draft when note changes (e.g. after save)
  useEffect(() => {
    if (!editing) setDraft(note || '')
  }, [note, editing])

  // Focus textarea when entering edit mode
  useEffect(() => {
    if (editing && textareaRef.current) {
      textareaRef.current.focus()
      textareaRef.current.selectionStart = textareaRef.current.value.length
    }
  }, [editing])

  const handleSave = () => {
    updateNote.mutate({ id: transactionId, note: draft }, {
      onSuccess: () => setEditing(false),
    })
  }

  const handleCancel = () => {
    setDraft(note || '')
    setEditing(false)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && e.metaKey) {
      e.preventDefault()
      handleSave()
    }
    if (e.key === 'Escape') {
      handleCancel()
    }
  }

  return (
    <section>
      <div className="flex items-center gap-2 mb-2">
        <h4 className="text-xs uppercase text-text-secondary">Note</h4>
        {noteSource === 'ibank_import' && (
          <span className="text-[10px] text-text-secondary bg-bg-primary px-1.5 py-0.5 rounded">iBank</span>
        )}
        {!editing && (
          <button
            onClick={() => setEditing(true)}
            className="text-text-secondary hover:text-accent text-xs ml-auto"
          >
            {note ? 'Edit' : '+ Add note'}
          </button>
        )}
      </div>

      {editing ? (
        <div className="space-y-2">
          <textarea
            ref={textareaRef}
            value={draft}
            onChange={e => setDraft(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={3}
            className="w-full bg-bg-primary border border-border rounded-md px-3 py-2 text-sm text-text-primary placeholder:text-text-secondary focus:outline-none focus:border-accent resize-y"
            placeholder="Add a note..."
          />
          <div className="flex gap-2 items-center">
            <button
              onClick={handleSave}
              disabled={updateNote.isPending}
              className="text-xs px-3 py-1 bg-accent text-white rounded-md hover:bg-accent-hover disabled:opacity-50"
            >
              {updateNote.isPending ? 'Saving...' : 'Save'}
            </button>
            <button
              onClick={handleCancel}
              className="text-xs px-3 py-1 text-text-secondary hover:text-text-primary"
            >
              Cancel
            </button>
            <span className="text-[10px] text-text-secondary ml-auto">Cmd+Enter to save</span>
          </div>
        </div>
      ) : note ? (
        <div className="text-text-primary whitespace-pre-wrap">{note}</div>
      ) : (
        <div className="text-text-secondary text-xs italic">No note</div>
      )}
    </section>
  )
}

function ReceiptSection({ transactionId }: { transactionId: string }) {
  const { data, isLoading } = useTransactionReceipts(transactionId)
  const uploadMut = useUploadReceipt()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [lightboxSrc, setLightboxSrc] = useState<{ src: string; mime: string; title: string } | null>(null)

  const handleUpload = (files: FileList | null) => {
    if (!files) return
    Array.from(files).forEach(file => {
      uploadMut.mutate({ file })
    })
  }

  const items = data?.items ?? []

  return (
    <section>
      <h4 className="text-xs uppercase text-text-secondary mb-2">Receipts</h4>

      {isLoading ? (
        <div className="text-xs text-text-secondary">Loading...</div>
      ) : items.length > 0 ? (
        <div className="flex flex-wrap gap-2 mb-2">
          {items.map(r => (
            <div
              key={r.id}
              className="cursor-pointer rounded border border-border hover:border-accent/50 overflow-hidden"
              onClick={() => setLightboxSrc({
                src: receiptFileUrl(r.id),
                mime: r.mime_type,
                title: r.original_filename,
              })}
              title={r.original_filename}
            >
              {r.mime_type.startsWith('image/') ? (
                <img
                  src={receiptThumbnailUrl(r.id)}
                  alt={r.original_filename}
                  className="w-16 h-16 object-cover"
                  onError={e => {
                    (e.target as HTMLImageElement).style.display = 'none'
                    ;(e.target as HTMLImageElement).parentElement!.innerHTML = '<span class="flex items-center justify-center w-16 h-16 text-2xl">📷</span>'
                  }}
                />
              ) : (
                <span className="flex items-center justify-center w-16 h-16 text-2xl">
                  {r.mime_type === 'application/pdf' ? '📄' : '📝'}
                </span>
              )}
            </div>
          ))}
        </div>
      ) : (
        <div className="text-text-secondary text-xs italic mb-2">No receipts attached</div>
      )}

      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept="image/jpeg,image/png,image/webp,image/gif,application/pdf,text/plain"
        className="hidden"
        onChange={e => handleUpload(e.target.files)}
      />
      <button
        onClick={() => fileInputRef.current?.click()}
        disabled={uploadMut.isPending}
        className="text-xs text-accent hover:underline disabled:opacity-50"
      >
        {uploadMut.isPending ? 'Uploading...' : '+ Attach Receipt'}
      </button>

      {lightboxSrc && (
        <Lightbox
          src={lightboxSrc.src}
          mimeType={lightboxSrc.mime}
          title={lightboxSrc.title}
          onClose={() => setLightboxSrc(null)}
        />
      )}
    </section>
  )
}
