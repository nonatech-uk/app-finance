import { useState, useEffect } from 'react'
import {
  useSwIncoming,
  useSwCandidates,
  useLinkExpense,
  useIgnoreExpense,
  useUnignoreExpense,
  useSwOutgoing,
  useSwGroups,
  usePushExpense,
} from '../hooks/useSplitwise'
import type { SwCandidate, OutgoingTransaction, SwGroup } from '../api/splitwise'
import LoadingSpinner from '../components/common/LoadingSpinner'
import CurrencyAmount from '../components/common/CurrencyAmount'

function formatDate(dateStr: string | null): string {
  if (!dateStr) return '—'
  return new Date(dateStr).toLocaleDateString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric',
  })
}

// ── Incoming Tab ────────────────────────────────────────────────────────────

function IncomingTab() {
  const [showAll, setShowAll] = useState(false)
  const [showIgnored, setShowIgnored] = useState(false)
  const [selectedId, setSelectedId] = useState<number | null>(null)

  const { data: items, isLoading } = useSwIncoming({ showAll, showIgnored })
  const { data: candidateData, isLoading: candidatesLoading } = useSwCandidates(selectedId)
  const linkMut = useLinkExpense()
  const ignoreMut = useIgnoreExpense()
  const unignoreMut = useUnignoreExpense()

  const selected = items?.find(e => e.id === selectedId)

  const handleLink = (expenseId: number, transactionId: string) => {
    linkMut.mutate({ expenseId, transactionId }, {
      onSuccess: () => setSelectedId(null),
    })
  }

  const handleIgnore = (expenseId: number) => {
    ignoreMut.mutate(expenseId, {
      onSuccess: () => setSelectedId(null),
    })
  }

  const handleUnignore = (expenseId: number) => {
    unignoreMut.mutate(expenseId, {
      onSuccess: () => setSelectedId(null),
    })
  }

  if (isLoading) return <LoadingSpinner />

  const list = items ?? []

  return (
    <div className="flex h-full">
      {/* List */}
      <div className="flex-1 overflow-auto p-5">
        <div className="flex items-center gap-4 mb-4">
          <span className="text-text-secondary text-sm">
            {list.length} expense{list.length !== 1 ? 's' : ''}
          </span>
          <div className="ml-auto flex items-center gap-4">
            <label className="flex items-center gap-2 text-sm text-text-secondary cursor-pointer">
              <input
                type="checkbox"
                checked={showAll}
                onChange={e => setShowAll(e.target.checked)}
                className="rounded"
              />
              All time
            </label>
            <label className="flex items-center gap-2 text-sm text-text-secondary cursor-pointer">
              <input
                type="checkbox"
                checked={showIgnored}
                onChange={e => setShowIgnored(e.target.checked)}
                className="rounded"
              />
              Show ignored
            </label>
          </div>
        </div>

        {list.length === 0 ? (
          <div className="text-text-secondary text-center py-12">
            No unsynced Splitwise expenses found.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-text-secondary border-b border-border">
                <th className="py-2 px-2">Date</th>
                <th className="py-2 px-2">Description</th>
                <th className="py-2 px-2 text-right">Amount</th>
                <th className="py-2 px-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {list.map(e => (
                <tr
                  key={e.id}
                  onClick={() => setSelectedId(e.id)}
                  className={`border-b border-border/50 cursor-pointer transition-colors ${
                    selectedId === e.id
                      ? 'bg-accent/10'
                      : 'hover:bg-bg-hover'
                  }`}
                >
                  <td className="py-2 px-2 text-text-secondary">{formatDate(e.date)}</td>
                  <td className="py-2 px-2">{e.description}</td>
                  <td className="py-2 px-2 text-right">
                    <CurrencyAmount amount={parseFloat(e.cost)} currency={e.currency_code} />
                  </td>
                  <td className="py-2 px-2">
                    <span className={`px-2 py-0.5 rounded-full text-xs ${
                      e.status === 'ignored'
                        ? 'bg-bg-hover text-text-secondary'
                        : 'bg-yellow-500/15 text-yellow-400'
                    }`}>
                      {e.status === 'ignored' ? 'Ignored' : 'Pending'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Detail drawer */}
      {selectedId !== null && (
        <div className="w-[480px] shrink-0 border-l border-border overflow-auto p-5 bg-bg-secondary">
          {candidatesLoading ? (
            <LoadingSpinner />
          ) : candidateData ? (
            <div>
              <div className="flex items-start justify-between mb-4">
                <h3 className="text-lg font-semibold">
                  {candidateData.expense.description}
                </h3>
                <button
                  onClick={() => setSelectedId(null)}
                  className="text-text-secondary hover:text-text-primary text-xl leading-none"
                >
                  x
                </button>
              </div>

              <div className="space-y-2 mb-6 text-sm">
                <div className="flex justify-between">
                  <span className="text-text-secondary">Amount</span>
                  <CurrencyAmount
                    amount={parseFloat(candidateData.expense.cost)}
                    currency={candidateData.expense.currency_code}
                  />
                </div>
                <div className="flex justify-between">
                  <span className="text-text-secondary">Date</span>
                  <span>{formatDate(candidateData.expense.date)}</span>
                </div>
                {candidateData.expense.original_currency && (
                  <div className="flex justify-between">
                    <span className="text-text-secondary">Original</span>
                    <span>{candidateData.expense.original_currency}</span>
                  </div>
                )}
                {candidateData.expense.details && (
                  <div className="flex justify-between">
                    <span className="text-text-secondary">Details</span>
                    <span className="text-right max-w-[280px]">{candidateData.expense.details}</span>
                  </div>
                )}
              </div>

              {/* Candidates */}
              <h4 className="text-sm font-medium text-text-secondary mb-3">
                Candidate Matches ({candidateData.candidates.length})
              </h4>

              {candidateData.candidates.length === 0 ? (
                <div className="text-text-secondary text-sm py-4 text-center">
                  No matching transactions found.
                </div>
              ) : (
                <div className="space-y-2">
                  {candidateData.candidates.map(c => (
                    <CandidateRow
                      key={c.id}
                      candidate={c}
                      onLink={() => handleLink(selectedId, c.id)}
                      linking={linkMut.isPending}
                    />
                  ))}
                </div>
              )}

              {/* Actions */}
              <div className="mt-6 pt-4 border-t border-border">
                {selected?.status === 'ignored' ? (
                  <button
                    onClick={() => handleUnignore(selectedId)}
                    disabled={unignoreMut.isPending}
                    className="w-full px-4 py-2 text-sm rounded-lg bg-accent/15 text-accent hover:bg-accent/25 transition-colors disabled:opacity-50"
                  >
                    {unignoreMut.isPending ? 'Restoring...' : 'Restore'}
                  </button>
                ) : (
                  <button
                    onClick={() => handleIgnore(selectedId)}
                    disabled={ignoreMut.isPending}
                    className="w-full px-4 py-2 text-sm rounded-lg bg-bg-hover text-text-secondary hover:bg-expense/15 hover:text-expense transition-colors disabled:opacity-50"
                  >
                    {ignoreMut.isPending ? 'Ignoring...' : 'Ignore'}
                  </button>
                )}
              </div>
            </div>
          ) : null}
        </div>
      )}
    </div>
  )
}

function CandidateRow({
  candidate: c,
  onLink,
  linking,
}: {
  candidate: SwCandidate
  onLink: () => void
  linking: boolean
}) {
  return (
    <div className={`flex items-center gap-3 p-3 rounded-lg border transition-colors ${
      c.already_linked
        ? 'border-border/50 opacity-50'
        : 'border-border hover:border-accent/50'
    }`}>
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium truncate">{c.merchant || '—'}</div>
        <div className="text-xs text-text-secondary flex gap-2">
          <span>{formatDate(c.date)}</span>
          <span>{c.institution}</span>
          {c.matched_via && (
            <span className="text-accent/70">{c.matched_via}</span>
          )}
        </div>
      </div>
      <div className="text-right shrink-0">
        <CurrencyAmount amount={Math.abs(parseFloat(c.amount))} currency={c.currency} />
      </div>
      {!c.already_linked && (
        <button
          onClick={e => { e.stopPropagation(); onLink() }}
          disabled={linking}
          className="shrink-0 px-3 py-1.5 text-xs rounded-lg bg-accent/15 text-accent hover:bg-accent/25 transition-colors disabled:opacity-50"
        >
          Link
        </button>
      )}
      {c.already_linked && (
        <span className="shrink-0 px-3 py-1.5 text-xs text-text-secondary">Linked</span>
      )}
    </div>
  )
}

// ── Outgoing Tab ────────────────────────────────────────────────────────────

function OutgoingTab() {
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const { data: items, isLoading } = useSwOutgoing()
  const { data: groupsData } = useSwGroups()

  const selected = items?.find(t => t.id === selectedId)

  if (isLoading) return <LoadingSpinner />

  const list = items ?? []

  return (
    <div className="flex h-full">
      {/* List */}
      <div className="flex-1 overflow-auto p-5">
        <div className="flex items-center gap-4 mb-4">
          <span className="text-text-secondary text-sm">
            {list.length} transaction{list.length !== 1 ? 's' : ''}
          </span>
        </div>

        {list.length === 0 ? (
          <div className="text-text-secondary text-center py-12">
            No unsynced splitwise-tagged transactions.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-text-secondary border-b border-border">
                <th className="py-2 px-2">Date</th>
                <th className="py-2 px-2">Merchant</th>
                <th className="py-2 px-2 text-right">Amount</th>
                <th className="py-2 px-2">Source</th>
              </tr>
            </thead>
            <tbody>
              {list.map(t => (
                <tr
                  key={t.id}
                  onClick={() => setSelectedId(t.id)}
                  className={`border-b border-border/50 cursor-pointer transition-colors ${
                    selectedId === t.id
                      ? 'bg-accent/10'
                      : 'hover:bg-bg-hover'
                  }`}
                >
                  <td className="py-2 px-2 text-text-secondary">{formatDate(t.date)}</td>
                  <td className="py-2 px-2">{t.merchant_name || t.raw_merchant}</td>
                  <td className="py-2 px-2 text-right">
                    <CurrencyAmount amount={Math.abs(parseFloat(t.amount))} currency={t.currency} />
                  </td>
                  <td className="py-2 px-2 text-text-secondary">{t.institution}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Detail drawer */}
      {selected && groupsData && (
        <OutgoingDrawer
          transaction={selected}
          groups={groupsData.groups}
          userId={groupsData.user_id}
          onClose={() => setSelectedId(null)}
        />
      )}
    </div>
  )
}

function OutgoingDrawer({
  transaction: t,
  groups,
  userId,
  onClose,
}: {
  transaction: OutgoingTransaction
  groups: SwGroup[]
  userId: number
  onClose: () => void
}) {
  const [groupId, setGroupId] = useState<number | null>(null)
  const [selectedMembers, setSelectedMembers] = useState<Set<number>>(new Set())
  const pushMut = usePushExpense()

  // Default to first group (most recently created) on mount
  const effectiveGroupId = groupId ?? groups[0]?.id ?? 0
  const selectedGroup = groups.find(g => g.id === effectiveGroupId)

  // When group changes, default-select all members
  useEffect(() => {
    if (selectedGroup) {
      setSelectedMembers(new Set(selectedGroup.members.map(m => m.id)))
    }
  }, [effectiveGroupId, selectedGroup])

  const handlePush = () => {
    if (selectedMembers.size === 0) return
    pushMut.mutate({
      transactionId: t.id,
      groupId: effectiveGroupId,
      memberIds: Array.from(selectedMembers),
    }, {
      onSuccess: () => onClose(),
    })
  }

  const toggleMember = (id: number) => {
    setSelectedMembers(prev => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }

  return (
    <div className="w-[480px] shrink-0 border-l border-border overflow-auto p-5 bg-bg-secondary">
      <div className="flex items-start justify-between mb-4">
        <h3 className="text-lg font-semibold">{t.merchant_name || t.raw_merchant}</h3>
        <button
          onClick={onClose}
          className="text-text-secondary hover:text-text-primary text-xl leading-none"
        >
          x
        </button>
      </div>

      <div className="space-y-2 mb-6 text-sm">
        <div className="flex justify-between">
          <span className="text-text-secondary">Amount</span>
          <CurrencyAmount amount={Math.abs(parseFloat(t.amount))} currency={t.currency} />
        </div>
        <div className="flex justify-between">
          <span className="text-text-secondary">Date</span>
          <span>{formatDate(t.date)}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-text-secondary">Source</span>
          <span>{t.institution}</span>
        </div>
        {t.category_path && (
          <div className="flex justify-between">
            <span className="text-text-secondary">Category</span>
            <span>{t.category_path}</span>
          </div>
        )}
        {t.note && (
          <div className="flex justify-between">
            <span className="text-text-secondary">Note</span>
            <span className="text-right max-w-[280px]">{t.note}</span>
          </div>
        )}
      </div>

      {/* Group selector */}
      <div className="mb-4">
        <label className="block text-sm font-medium text-text-secondary mb-2">
          Splitwise Group
        </label>
        <select
          value={effectiveGroupId}
          onChange={e => setGroupId(Number(e.target.value))}
          className="w-full px-3 py-2 text-sm rounded-lg bg-bg-primary border border-border text-text-primary"
        >
          {groups.map(g => (
            <option key={g.id} value={g.id}>{g.name}</option>
          ))}
        </select>
      </div>

      {/* Member checkboxes */}
      {selectedGroup && selectedGroup.members.length > 1 && (
        <div className="mb-6">
          <label className="block text-sm font-medium text-text-secondary mb-2">
            Split between
          </label>
          <div className="space-y-1">
            {selectedGroup.members.map(m => (
              <label key={m.id} className="flex items-center gap-2 text-sm cursor-pointer py-1">
                <input
                  type="checkbox"
                  checked={selectedMembers.has(m.id)}
                  onChange={() => toggleMember(m.id)}
                  className="rounded"
                />
                <span>{m.name}</span>
                {m.id === userId && (
                  <span className="text-xs text-text-secondary">(you)</span>
                )}
              </label>
            ))}
          </div>
          {selectedMembers.size > 0 && (
            <div className="mt-2 text-xs text-text-secondary">
              {Math.abs(parseFloat(t.amount)) / selectedMembers.size > 0
                ? `${t.currency} ${(Math.abs(parseFloat(t.amount)) / selectedMembers.size).toFixed(2)} each`
                : ''}
            </div>
          )}
        </div>
      )}

      {/* Push button */}
      <button
        onClick={handlePush}
        disabled={pushMut.isPending || selectedMembers.size === 0}
        className="w-full px-4 py-2 text-sm rounded-lg bg-accent/15 text-accent hover:bg-accent/25 transition-colors disabled:opacity-50"
      >
        {pushMut.isPending ? 'Creating...' : 'Push to Splitwise'}
      </button>
      {pushMut.isError && (
        <div className="text-expense text-sm mt-2">
          Error: {(pushMut.error as Error).message}
        </div>
      )}
    </div>
  )
}

// ── Main Page ───────────────────────────────────────────────────────────────

const TABS = [
  { key: 'incoming', label: 'Incoming' },
  { key: 'outgoing', label: 'Outgoing' },
] as const

type TabKey = typeof TABS[number]['key']

export default function Splitwise() {
  const [tab, setTab] = useState<TabKey>('incoming')

  return (
    <div className="flex flex-col h-full">
      <div className="px-5 pt-5 pb-0">
        <h2 className="text-xl font-semibold mb-4">Splitwise</h2>
        <div className="flex gap-2">
          {TABS.map(t => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`px-4 py-1.5 text-sm rounded-full transition-colors ${
                tab === t.key
                  ? 'bg-accent/15 text-accent font-medium'
                  : 'text-text-secondary hover:text-text-primary hover:bg-bg-hover'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>
      <div className="flex-1 min-h-0">
        {tab === 'incoming' ? <IncomingTab /> : <OutgoingTab />}
      </div>
    </div>
  )
}
