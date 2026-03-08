import { useCallback, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAccounts, useUpdateAccount } from '../hooks/useAccounts'
import { useCsvPreview, useCsvConfirm } from '../hooks/useImports'
import { useScope } from '../contexts/ScopeContext'
import CurrencyAmount from '../components/common/CurrencyAmount'
import Badge from '../components/common/Badge'
import LoadingSpinner from '../components/common/LoadingSpinner'
import type { AccountItem, CsvPreviewResult } from '../api/types'

function AccountEditModal({
  account,
  onClose,
}: {
  account: AccountItem
  onClose: () => void
}) {
  const updateAccount = useUpdateAccount()
  const [displayName, setDisplayName] = useState(account.display_name || '')
  const [isArchived, setIsArchived] = useState(account.is_archived)
  const [excludeFromReports, setExcludeFromReports] = useState(account.exclude_from_reports)
  const [isFavourite, setIsFavourite] = useState(account.is_favourite)
  const [displayOrder, setDisplayOrder] = useState(account.display_order ?? 100)

  const handleSave = () => {
    updateAccount.mutate(
      {
        institution: account.institution,
        accountRef: account.account_ref,
        body: {
          display_name: displayName || null,
          is_archived: isArchived,
          exclude_from_reports: excludeFromReports,
          is_favourite: isFavourite,
          display_order: isFavourite ? displayOrder : null,
        },
      },
      { onSuccess: onClose },
    )
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-bg-card border border-border rounded-lg p-6 w-96 space-y-4"
        onClick={e => e.stopPropagation()}
      >
        <h3 className="text-lg font-semibold">
          Edit Account
        </h3>
        <p className="text-text-secondary text-sm">
          {account.institution} / {account.account_ref}
        </p>

        <div>
          <label className="block text-sm text-text-secondary mb-1">Display Name</label>
          <input
            type="text"
            value={displayName}
            onChange={e => setDisplayName(e.target.value)}
            placeholder={account.name || account.account_ref}
            className="w-full bg-bg-hover border border-border rounded px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent"
          />
        </div>

        <label className="flex items-center gap-2 text-sm cursor-pointer">
          <input
            type="checkbox"
            checked={isArchived}
            onChange={e => setIsArchived(e.target.checked)}
            className="accent-accent"
          />
          <span>Archived</span>
          <span className="text-text-secondary text-xs">(hide from dashboard)</span>
        </label>

        <label className="flex items-center gap-2 text-sm cursor-pointer">
          <input
            type="checkbox"
            checked={excludeFromReports}
            onChange={e => setExcludeFromReports(e.target.checked)}
            className="accent-accent"
          />
          <span>Exclude from reports</span>
          <span className="text-text-secondary text-xs">(category spending)</span>
        </label>

        <label className="flex items-center gap-2 text-sm cursor-pointer">
          <input
            type="checkbox"
            checked={isFavourite}
            onChange={e => setIsFavourite(e.target.checked)}
            className="accent-accent"
          />
          <span>Favourite</span>
          <span className="text-text-secondary text-xs">(show on dashboard)</span>
        </label>

        {isFavourite && (
          <div>
            <label className="block text-sm text-text-secondary mb-1">Priority</label>
            <input
              type="number"
              value={displayOrder}
              onChange={e => setDisplayOrder(parseInt(e.target.value) || 100)}
              min={1}
              className="w-24 bg-bg-hover border border-border rounded px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent"
            />
            <span className="text-text-secondary text-xs ml-2">Lower = higher priority</span>
          </div>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm rounded border border-border hover:bg-bg-hover transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={updateAccount.isPending}
            className="px-4 py-2 text-sm rounded bg-accent text-white hover:bg-accent/80 transition-colors disabled:opacity-50"
          >
            {updateAccount.isPending ? 'Saving...' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}


function CsvImportModal({
  account,
  onClose,
}: {
  account: AccountItem
  onClose: () => void
}) {
  const preview = useCsvPreview()
  const confirm = useCsvConfirm()
  const fileRef = useRef<HTMLInputElement>(null)
  const [dragActive, setDragActive] = useState(false)
  const [previewData, setPreviewData] = useState<CsvPreviewResult | null>(null)
  const [importDone, setImportDone] = useState(false)

  const handleFile = useCallback(
    (file: File) => {
      setPreviewData(null)
      setImportDone(false)
      preview.mutate(
        {
          file,
          institution: account.institution,
          accountRef: account.account_ref,
        },
        {
          onSuccess: data => setPreviewData(data),
        },
      )
    },
    [account, preview],
  )

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      setDragActive(false)
      const file = e.dataTransfer.files[0]
      if (file) handleFile(file)
    },
    [handleFile],
  )

  const handleConfirm = () => {
    confirm.mutate(
      {
        institution: account.institution,
        accountRef: account.account_ref,
      },
      {
        onSuccess: () => setImportDone(true),
      },
    )
  }

  const accountLabel = account.display_name || account.name || account.account_ref

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-bg-card border border-border rounded-lg p-6 w-[36rem] max-h-[80vh] overflow-y-auto space-y-4"
        onClick={e => e.stopPropagation()}
      >
        <div>
          <h3 className="text-lg font-semibold">Import CSV</h3>
          <p className="text-text-secondary text-sm">
            {account.institution} / {accountLabel}
          </p>
        </div>

        {/* Drop zone */}
        {!previewData && !importDone && (
          <div
            onDragEnter={() => setDragActive(true)}
            onDragLeave={() => setDragActive(false)}
            onDragOver={e => e.preventDefault()}
            onDrop={handleDrop}
            onClick={() => fileRef.current?.click()}
            className={`border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors ${
              dragActive
                ? 'border-accent bg-accent/5'
                : 'border-border hover:border-text-secondary'
            }`}
          >
            <input
              ref={fileRef}
              type="file"
              accept=".csv"
              className="hidden"
              onChange={e => {
                const file = e.target.files?.[0]
                if (file) handleFile(file)
              }}
            />
            {preview.isPending ? (
              <div className="flex items-center justify-center gap-2">
                <LoadingSpinner />
                <span className="text-sm text-text-secondary">Analysing CSV...</span>
              </div>
            ) : (
              <>
                <p className="text-text-secondary text-sm">
                  Drop a CSV file here or click to browse
                </p>
                <p className="text-text-secondary text-xs mt-1">
                  Supports First Direct, Marcus, Wise, and Monzo exports
                </p>
              </>
            )}
          </div>
        )}

        {/* Error */}
        {preview.isError && (
          <div className="bg-expense/10 border border-expense/30 rounded p-3 text-sm text-expense">
            {preview.error instanceof Error ? preview.error.message : 'Upload failed'}
          </div>
        )}

        {/* Preview results */}
        {previewData && !importDone && (
          <div className="space-y-4">
            {/* Summary */}
            <div className="grid grid-cols-3 gap-3">
              <div className="bg-bg-hover rounded p-3 text-center">
                <div className="text-lg font-semibold">{previewData.total_rows}</div>
                <div className="text-xs text-text-secondary">Total rows</div>
              </div>
              <div className="bg-bg-hover rounded p-3 text-center">
                <div className="text-lg font-semibold text-income">{previewData.new_count}</div>
                <div className="text-xs text-text-secondary">New</div>
              </div>
              <div className="bg-bg-hover rounded p-3 text-center">
                <div className="text-lg font-semibold">{previewData.existing_count}</div>
                <div className="text-xs text-text-secondary">Already in DB</div>
              </div>
            </div>

            <p className="text-xs text-text-secondary">
              Detected format: <Badge>{previewData.format}</Badge>
            </p>

            {/* Mismatches */}
            {previewData.mismatch_count > 0 && (
              <div>
                <h4 className="text-sm font-medium text-warning mb-2">
                  {previewData.mismatch_count} amount mismatch{previewData.mismatch_count > 1 ? 'es' : ''}
                </h4>
                <div className="max-h-32 overflow-y-auto border border-border rounded">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-border text-left text-text-secondary">
                        <th className="px-2 py-1">Date</th>
                        <th className="px-2 py-1">Merchant</th>
                        <th className="px-2 py-1 text-right">CSV</th>
                        <th className="px-2 py-1 text-right">DB</th>
                      </tr>
                    </thead>
                    <tbody>
                      {previewData.mismatches.map((m, i) => (
                        <tr key={i} className="border-b border-border last:border-0">
                          <td className="px-2 py-1">{m.posted_at}</td>
                          <td className="px-2 py-1 truncate max-w-[10rem]">{m.raw_merchant}</td>
                          <td className="px-2 py-1 text-right text-warning">{m.csv_amount}</td>
                          <td className="px-2 py-1 text-right">{m.db_amount}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* New transactions */}
            {previewData.new_count > 0 && (
              <div>
                <h4 className="text-sm font-medium mb-2">
                  {previewData.new_count} new transaction{previewData.new_count > 1 ? 's' : ''} to import
                </h4>
                <div className="max-h-48 overflow-y-auto border border-border rounded">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-border text-left text-text-secondary sticky top-0 bg-bg-card">
                        <th className="px-2 py-1">Date</th>
                        <th className="px-2 py-1">Merchant</th>
                        <th className="px-2 py-1 text-right">Amount</th>
                      </tr>
                    </thead>
                    <tbody>
                      {previewData.new_transactions.map((t, i) => (
                        <tr key={i} className="border-b border-border last:border-0">
                          <td className="px-2 py-1">{t.posted_at}</td>
                          <td className="px-2 py-1 truncate max-w-[14rem]">{t.raw_merchant}</td>
                          <td className="px-2 py-1 text-right">
                            <CurrencyAmount amount={t.amount} currency={t.currency} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* Actions */}
            <div className="flex justify-between items-center pt-2">
              <button
                onClick={() => {
                  setPreviewData(null)
                  preview.reset()
                  if (fileRef.current) fileRef.current.value = ''
                }}
                className="text-sm text-text-secondary hover:text-text-primary transition-colors"
              >
                Upload different file
              </button>
              <div className="flex gap-2">
                <button
                  onClick={onClose}
                  className="px-4 py-2 text-sm rounded border border-border hover:bg-bg-hover transition-colors"
                >
                  Cancel
                </button>
                {previewData.new_count > 0 && (
                  <button
                    onClick={handleConfirm}
                    disabled={confirm.isPending}
                    className="px-4 py-2 text-sm rounded bg-accent text-white hover:bg-accent/80 transition-colors disabled:opacity-50"
                  >
                    {confirm.isPending ? (
                      <span className="flex items-center gap-2">
                        <LoadingSpinner /> Importing...
                      </span>
                    ) : (
                      `Import ${previewData.new_count} transaction${previewData.new_count > 1 ? 's' : ''}`
                    )}
                  </button>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Nothing new */}
        {previewData && previewData.new_count === 0 && !importDone && (
          <div className="text-center py-4">
            <p className="text-sm text-text-secondary">
              All {previewData.existing_count} transactions already exist in the database.
            </p>
            <button
              onClick={onClose}
              className="mt-3 px-4 py-2 text-sm rounded border border-border hover:bg-bg-hover transition-colors"
            >
              Close
            </button>
          </div>
        )}

        {/* Import complete */}
        {importDone && confirm.data && (
          <div className="text-center py-4 space-y-3">
            <div className="text-income text-lg font-semibold">
              Import complete
            </div>
            <p className="text-sm text-text-secondary">
              {confirm.data.inserted} transaction{confirm.data.inserted !== 1 ? 's' : ''} imported,{' '}
              {confirm.data.skipped} skipped.
            </p>
            <button
              onClick={onClose}
              className="px-4 py-2 text-sm rounded bg-accent text-white hover:bg-accent/80 transition-colors"
            >
              Done
            </button>
          </div>
        )}

        {/* Confirm error */}
        {confirm.isError && (
          <div className="bg-expense/10 border border-expense/30 rounded p-3 text-sm text-expense">
            {confirm.error instanceof Error ? confirm.error.message : 'Import failed'}
          </div>
        )}
      </div>
    </div>
  )
}


export default function Accounts() {
  const { scope } = useScope()
  const [showArchived, setShowArchived] = useState(false)
  const [editAccount, setEditAccount] = useState<AccountItem | null>(null)
  const [importAccount, setImportAccount] = useState<AccountItem | null>(null)
  const { data, isLoading } = useAccounts(showArchived, scope)

  const grouped = useMemo(() => {
    if (!data) return {}
    const groups: Record<string, typeof data.items> = {}
    for (const item of data.items) {
      const key = item.institution
      if (!groups[key]) groups[key] = []
      groups[key].push(item)
    }
    return groups
  }, [data])

  if (isLoading) return <LoadingSpinner />

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold">Accounts</h2>
        <label className="flex items-center gap-2 text-sm text-text-secondary cursor-pointer">
          <input
            type="checkbox"
            checked={showArchived}
            onChange={e => setShowArchived(e.target.checked)}
            className="accent-accent"
          />
          Show archived
        </label>
      </div>

      {Object.entries(grouped).map(([institution, accounts]) => (
        <div key={institution}>
          <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">{institution}</h3>
          <div className="grid grid-cols-3 gap-4">
            {accounts.map(acct => (
              <div
                key={`${acct.institution}-${acct.account_ref}`}
                className={`bg-bg-card border border-border rounded-lg p-4 transition-colors relative ${
                  acct.is_archived ? 'opacity-50' : 'hover:border-accent/50'
                }`}
              >
                {/* Action buttons */}
                <div className="absolute top-2 right-2 flex gap-0.5">
                  <button
                    onClick={e => {
                      e.preventDefault()
                      setImportAccount(acct)
                    }}
                    className="p-1.5 rounded hover:bg-bg-hover text-text-secondary hover:text-text-primary transition-colors"
                    title="Import CSV"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" className="w-3.5 h-3.5">
                      <path d="M8 1a.75.75 0 0 1 .75.75v6.5a.75.75 0 0 1-1.5 0v-6.5A.75.75 0 0 1 8 1Z" />
                      <path d="M4.78 4.97a.75.75 0 0 1 0 1.06L3.81 7.08h0L8 7.08a.75.75 0 0 1 0 1.5H3.81l.97.97a.75.75 0 0 1-1.06 1.06l-2.25-2.25a.75.75 0 0 1 0-1.06l2.25-2.25a.75.75 0 0 1 1.06 0Z" />
                      <path d="M2 13.25a.75.75 0 0 1 .75-.75h10.5a.75.75 0 0 1 0 1.5H2.75a.75.75 0 0 1-.75-.75Z" />
                    </svg>
                  </button>
                  <button
                    onClick={e => {
                      e.preventDefault()
                      setEditAccount(acct)
                    }}
                    className="p-1.5 rounded hover:bg-bg-hover text-text-secondary hover:text-text-primary transition-colors"
                    title="Edit account"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" className="w-3.5 h-3.5">
                      <path d="M13.488 2.513a1.75 1.75 0 0 0-2.475 0L6.75 6.774a2.75 2.75 0 0 0-.596.892l-.848 2.047a.75.75 0 0 0 .98.98l2.047-.848a2.75 2.75 0 0 0 .892-.596l4.261-4.262a1.75 1.75 0 0 0 0-2.474Z" />
                      <path d="M4.75 3.5c-.69 0-1.25.56-1.25 1.25v6.5c0 .69.56 1.25 1.25 1.25h6.5c.69 0 1.25-.56 1.25-1.25V9A.75.75 0 0 1 14 9v2.25A2.75 2.75 0 0 1 11.25 14h-6.5A2.75 2.75 0 0 1 2 11.25v-6.5A2.75 2.75 0 0 1 4.75 2H7a.75.75 0 0 1 0 1.5H4.75Z" />
                    </svg>
                  </button>
                </div>

                <Link
                  to={`/accounts/${acct.institution}/${acct.account_ref}`}
                  className="block"
                >
                  <div className="flex justify-between items-start mb-2 pr-14">
                    <div className="font-medium truncate flex items-center gap-1">
                      {acct.is_favourite && (
                        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4 text-amber-400 shrink-0">
                          <path fillRule="evenodd" d="M10.868 2.884c-.321-.772-1.415-.772-1.736 0l-1.83 4.401-4.753.381c-.833.067-1.171 1.107-.536 1.651l3.62 3.102-1.106 4.637c-.194.813.691 1.456 1.405 1.02L10 15.591l4.069 2.485c.713.436 1.598-.207 1.404-1.02l-1.106-4.637 3.62-3.102c.635-.544.297-1.584-.536-1.65l-4.752-.382-1.831-4.401Z" clipRule="evenodd" />
                        </svg>
                      )}
                      {acct.display_name || acct.name || acct.account_ref}
                    </div>
                    <Badge>{acct.currency}</Badge>
                  </div>
                  {acct.balance && (
                    <div className="text-lg mb-2">
                      <CurrencyAmount amount={acct.balance} currency={acct.currency} showSign={false} />
                    </div>
                  )}
                  <div className="text-text-secondary text-xs space-y-0.5">
                    <div>{acct.transaction_count.toLocaleString()} transactions</div>
                    {acct.earliest_date && acct.latest_date && (
                      <div>{acct.earliest_date} — {acct.latest_date}</div>
                    )}
                    <div className="flex gap-1 flex-wrap">
                      {acct.account_type && <Badge variant="default">{acct.account_type}</Badge>}
                      {acct.is_archived && <Badge variant="warning">archived</Badge>}
                      {acct.exclude_from_reports && <Badge variant="accent">excl. reports</Badge>}
                    </div>
                  </div>
                </Link>
              </div>
            ))}
          </div>
        </div>
      ))}

      {editAccount && (
        <AccountEditModal account={editAccount} onClose={() => setEditAccount(null)} />
      )}
      {importAccount && (
        <CsvImportModal account={importAccount} onClose={() => setImportAccount(null)} />
      )}
    </div>
  )
}
