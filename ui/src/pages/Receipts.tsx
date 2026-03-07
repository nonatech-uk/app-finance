import { useState, useRef, useCallback } from 'react'
import {
  useReceipts,
  useReceipt,
  useUploadReceipt,
  useMatchReceipt,
  useUnmatchReceipt,
  useDeleteReceipt,
  useCandidates,
} from '../hooks/useReceipts'
import { receiptFileUrl, receiptThumbnailUrl } from '../api/receipts'
import type { ReceiptCandidate } from '../api/receipts'
import LoadingSpinner from '../components/common/LoadingSpinner'
import CurrencyAmount from '../components/common/CurrencyAmount'
import Lightbox from '../components/common/Lightbox'

const STATUS_LABELS: Record<string, string> = {
  pending_ocr: 'Pending OCR',
  pending_match: 'Pending Match',
  auto_matched: 'Auto Matched',
  manually_matched: 'Matched',
  unmatched: 'Unmatched',
}

const STATUS_COLOURS: Record<string, string> = {
  pending_ocr: 'bg-yellow-500/15 text-yellow-400',
  pending_match: 'bg-yellow-500/15 text-yellow-400',
  auto_matched: 'bg-income/15 text-income',
  manually_matched: 'bg-income/15 text-income',
  unmatched: 'bg-expense/15 text-expense',
}

const FILTER_OPTIONS = [
  { value: 'all', label: 'All' },
  { value: 'pending_match', label: 'Pending' },
  { value: 'auto_matched', label: 'Auto Matched' },
  { value: 'manually_matched', label: 'Matched' },
  { value: 'unmatched', label: 'Unmatched' },
]

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatDate(dateStr: string | null): string {
  if (!dateStr) return '—'
  return new Date(dateStr).toLocaleDateString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric',
  })
}

export default function Receipts() {
  const [filter, setFilter] = useState('all')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [lightboxSrc, setLightboxSrc] = useState<{ src: string; mime: string; title: string } | null>(null)
  const [showCandidates, setShowCandidates] = useState(false)
  const [isDragging, setIsDragging] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const { data, isLoading } = useReceipts(filter)
  const { data: detail, isLoading: detailLoading } = useReceipt(selectedId)
  const uploadMut = useUploadReceipt()
  const matchMut = useMatchReceipt()
  const unmatchMut = useUnmatchReceipt()
  const deleteMut = useDeleteReceipt()
  const { data: candidatesData, isLoading: candidatesLoading } = useCandidates(
    showCandidates ? selectedId : null,
  )

  const handleFiles = useCallback((files: FileList | null) => {
    if (!files) return
    Array.from(files).forEach(file => {
      uploadMut.mutate({ file })
    })
  }, [uploadMut])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setIsDragging(false)
    handleFiles(e.dataTransfer.files)
  }, [handleFiles])

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setIsDragging(true)
  }, [])

  const handleDragLeave = useCallback(() => {
    setIsDragging(false)
  }, [])

  const openLightbox = (receipt: { id: string; mime_type: string; original_filename: string }) => {
    setLightboxSrc({
      src: receiptFileUrl(receipt.id),
      mime: receipt.mime_type,
      title: receipt.original_filename,
    })
  }

  const handleMatch = (receiptId: string, transactionId: string) => {
    matchMut.mutate({ receiptId, transactionId }, {
      onSuccess: () => setShowCandidates(false),
    })
  }

  const handleUnmatch = (receiptId: string) => {
    unmatchMut.mutate(receiptId)
  }

  const handleDelete = (receiptId: string) => {
    if (!confirm('Delete this receipt?')) return
    deleteMut.mutate(receiptId, {
      onSuccess: () => setSelectedId(null),
    })
  }

  if (isLoading) return <LoadingSpinner />

  const items = data?.items ?? []

  return (
    <div className="flex h-full">
      {/* Main content */}
      <div className="flex-1 overflow-auto p-5">
        <h2 className="text-xl font-semibold mb-4">Receipts</h2>

        {/* Upload zone */}
        <div
          className={`border-2 border-dashed rounded-lg p-8 mb-6 text-center cursor-pointer transition-colors ${
            isDragging
              ? 'border-accent bg-accent/10'
              : 'border-border hover:border-accent/50 hover:bg-bg-hover'
          }`}
          onDrop={handleDrop}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onClick={() => fileInputRef.current?.click()}
        >
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept="image/jpeg,image/png,image/webp,image/gif,application/pdf,text/plain"
            className="hidden"
            onChange={e => handleFiles(e.target.files)}
          />
          <div className="text-2xl mb-2">
            {uploadMut.isPending ? '...' : '📄'}
          </div>
          <div className="text-text-secondary text-sm">
            {uploadMut.isPending
              ? 'Uploading...'
              : 'Drop receipt files here or click to upload'}
          </div>
          <div className="text-text-secondary text-xs mt-1">
            JPEG, PNG, WebP, GIF, PDF, or TXT
          </div>
          {uploadMut.isError && (
            <div className="text-expense text-sm mt-2">
              Upload failed: {(uploadMut.error as Error).message}
            </div>
          )}
        </div>

        {/* Filters */}
        <div className="flex gap-2 mb-4">
          {FILTER_OPTIONS.map(opt => (
            <button
              key={opt.value}
              onClick={() => setFilter(opt.value)}
              className={`px-3 py-1.5 text-sm rounded-full transition-colors ${
                filter === opt.value
                  ? 'bg-accent/15 text-accent font-medium'
                  : 'text-text-secondary hover:text-text-primary hover:bg-bg-hover'
              }`}
            >
              {opt.label}
            </button>
          ))}
          {data && (
            <span className="ml-auto text-text-secondary text-sm self-center">
              {data.total} receipt{data.total !== 1 ? 's' : ''}
            </span>
          )}
        </div>

        {/* Receipt list */}
        {items.length === 0 ? (
          <div className="text-text-secondary text-center py-12">
            No receipts found.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-text-secondary border-b border-border">
                <th className="py-2 px-2 w-10"></th>
                <th className="py-2 px-2">File</th>
                <th className="py-2 px-2">Merchant</th>
                <th className="py-2 px-2 text-right">Amount</th>
                <th className="py-2 px-2">Date</th>
                <th className="py-2 px-2">Status</th>
                <th className="py-2 px-2">Uploaded</th>
              </tr>
            </thead>
            <tbody>
              {items.map(r => (
                <tr
                  key={r.id}
                  onClick={() => setSelectedId(r.id)}
                  className={`border-b border-border/50 cursor-pointer transition-colors ${
                    selectedId === r.id
                      ? 'bg-accent/10'
                      : 'hover:bg-bg-hover'
                  }`}
                >
                  <td className="py-2 px-2">
                    {r.mime_type.startsWith('image/') ? (
                      <img
                        src={receiptThumbnailUrl(r.id)}
                        alt=""
                        className="w-8 h-8 object-cover rounded"
                        onError={e => {
                          ;(e.target as HTMLImageElement).style.display = 'none'
                        }}
                      />
                    ) : (
                      <span className="text-lg">
                        {r.mime_type === 'application/pdf' ? '📄' : '📝'}
                      </span>
                    )}
                  </td>
                  <td className="py-2 px-2 truncate max-w-[200px]" title={r.original_filename}>
                    {r.original_filename}
                  </td>
                  <td className="py-2 px-2 text-text-secondary">
                    {r.extracted_merchant || '—'}
                  </td>
                  <td className="py-2 px-2 text-right">
                    {r.extracted_amount != null ? (
                      <CurrencyAmount
                        amount={r.extracted_amount}
                        currency={r.extracted_currency || ''}
                      />
                    ) : '—'}
                  </td>
                  <td className="py-2 px-2 text-text-secondary">
                    {formatDate(r.extracted_date)}
                  </td>
                  <td className="py-2 px-2">
                    <span className={`px-2 py-0.5 rounded-full text-xs ${STATUS_COLOURS[r.match_status] || 'bg-bg-hover text-text-secondary'}`}>
                      {STATUS_LABELS[r.match_status] || r.match_status}
                    </span>
                  </td>
                  <td className="py-2 px-2 text-text-secondary text-xs">
                    {formatDate(r.uploaded_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Detail sidebar */}
      {selectedId && (
        <div className="w-[480px] shrink-0 border-l border-border overflow-auto p-5 bg-bg-secondary">
          {detailLoading ? (
            <LoadingSpinner />
          ) : detail ? (
            <ReceiptDetailPanel
              detail={detail}
              onOpenLightbox={() => openLightbox(detail)}
              onMatch={(txnId) => handleMatch(detail.id, txnId)}
              onUnmatch={() => handleUnmatch(detail.id)}
              onDelete={() => handleDelete(detail.id)}
              onClose={() => setSelectedId(null)}
              showCandidates={showCandidates}
              onToggleCandidates={() => setShowCandidates(!showCandidates)}
              candidates={candidatesData?.candidates}
              candidatesLoading={candidatesLoading}
              matchPending={matchMut.isPending}
            />
          ) : null}
        </div>
      )}

      {/* Lightbox */}
      {lightboxSrc && (
        <Lightbox
          src={lightboxSrc.src}
          mimeType={lightboxSrc.mime}
          title={lightboxSrc.title}
          onClose={() => setLightboxSrc(null)}
        />
      )}
    </div>
  )
}

// ── Detail Panel ────────────────────────────────────────────────────────────

interface DetailPanelProps {
  detail: import('../api/receipts').ReceiptDetail
  onOpenLightbox: () => void
  onMatch: (txnId: string) => void
  onUnmatch: () => void
  onDelete: () => void
  onClose: () => void
  showCandidates: boolean
  onToggleCandidates: () => void
  candidates?: ReceiptCandidate[]
  candidatesLoading: boolean
  matchPending: boolean
}

function ReceiptDetailPanel({
  detail,
  onOpenLightbox,
  onMatch,
  onUnmatch,
  onDelete,
  onClose,
  showCandidates,
  onToggleCandidates,
  candidates,
  candidatesLoading,
  matchPending,
}: DetailPanelProps) {
  return (
    <div>
      <div className="flex justify-between items-center mb-4">
        <h3 className="text-lg font-semibold truncate">{detail.original_filename}</h3>
        <button
          onClick={onClose}
          className="text-text-secondary hover:text-text-primary text-xl"
        >
          &times;
        </button>
      </div>

      {/* Preview */}
      <div
        className="mb-4 cursor-pointer rounded overflow-hidden bg-bg-hover flex items-center justify-center"
        onClick={onOpenLightbox}
        style={{ minHeight: '200px' }}
      >
        {detail.mime_type.startsWith('image/') ? (
          <img
            src={receiptFileUrl(detail.id)}
            alt={detail.original_filename}
            className="max-w-full max-h-[300px] object-contain"
          />
        ) : (
          <div className="text-4xl py-8">
            {detail.mime_type === 'application/pdf' ? '📄' : '📝'}
            <div className="text-sm text-text-secondary mt-2">Click to view</div>
          </div>
        )}
      </div>

      {/* Status */}
      <div className="mb-4">
        <span className={`px-2 py-0.5 rounded-full text-xs ${STATUS_COLOURS[detail.match_status] || 'bg-bg-hover text-text-secondary'}`}>
          {STATUS_LABELS[detail.match_status] || detail.match_status}
        </span>
        <span className="text-xs text-text-secondary ml-2">{formatBytes(detail.file_size)}</span>
      </div>

      {/* OCR Data */}
      <div className="space-y-2 mb-4">
        <h4 className="text-sm font-medium text-text-secondary">Extracted Data</h4>
        <div className="grid grid-cols-2 gap-2 text-sm">
          <div>
            <span className="text-text-secondary">Merchant:</span>
            <div>{detail.extracted_merchant || '—'}</div>
          </div>
          <div>
            <span className="text-text-secondary">Date:</span>
            <div>{formatDate(detail.extracted_date)}</div>
          </div>
          <div>
            <span className="text-text-secondary">Amount:</span>
            <div>
              {detail.extracted_amount != null ? (
                <CurrencyAmount
                  amount={detail.extracted_amount}
                  currency={detail.extracted_currency || ''}
                />
              ) : '—'}
            </div>
          </div>
          <div>
            <span className="text-text-secondary">Currency:</span>
            <div>{detail.extracted_currency || '—'}</div>
          </div>
        </div>
      </div>

      {/* Matched Transaction */}
      {detail.matched_transaction && (
        <div className="mb-4 p-3 rounded bg-bg-hover border border-border">
          <h4 className="text-sm font-medium text-text-secondary mb-2">Matched Transaction</h4>
          <div className="text-sm">
            <div className="font-medium">{detail.matched_transaction.raw_merchant || 'Unknown'}</div>
            <div className="flex justify-between mt-1">
              <span className="text-text-secondary">{detail.matched_transaction.posted_at}</span>
              <CurrencyAmount
                amount={parseFloat(detail.matched_transaction.amount)}
                currency={detail.matched_transaction.currency || ''}
              />
            </div>
            <div className="text-xs text-text-secondary mt-1">
              {detail.matched_transaction.institution} / {detail.matched_transaction.account_ref}
            </div>
          </div>
          <button
            onClick={onUnmatch}
            className="mt-2 text-xs text-expense hover:underline"
          >
            Remove match
          </button>
        </div>
      )}

      {/* Find Match / Candidates */}
      {!detail.matched_transaction_id && (
        <div className="mb-4">
          <button
            onClick={onToggleCandidates}
            className="w-full px-3 py-2 text-sm bg-accent/15 text-accent rounded hover:bg-accent/25 transition-colors"
          >
            {showCandidates ? 'Hide Candidates' : 'Find Match'}
          </button>

          {showCandidates && (
            <div className="mt-3">
              {candidatesLoading ? (
                <LoadingSpinner />
              ) : candidates && candidates.length > 0 ? (
                <div className="space-y-2">
                  {candidates.map(c => (
                    <div
                      key={c.id}
                      className="p-2 rounded border border-border hover:border-accent/50 flex items-center justify-between text-sm"
                    >
                      <div>
                        <div className="font-medium">{c.raw_merchant || 'Unknown'}</div>
                        <div className="text-xs text-text-secondary">
                          {c.posted_at} &middot; {c.institution}/{c.account_ref}
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <CurrencyAmount
                          amount={parseFloat(c.amount)}
                          currency={c.currency || ''}
                        />
                        <button
                          onClick={() => onMatch(c.id)}
                          disabled={matchPending}
                          className="px-2 py-1 text-xs bg-accent text-white rounded hover:bg-accent/80 disabled:opacity-50"
                        >
                          Match
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-text-secondary text-sm text-center py-4">
                  No matching candidates found.
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* OCR Raw Text (collapsible) */}
      {detail.ocr_text && (
        <details className="mb-4">
          <summary className="text-sm text-text-secondary cursor-pointer hover:text-text-primary">
            OCR Raw Text
          </summary>
          <pre className="mt-2 p-3 bg-bg-hover rounded text-xs overflow-auto max-h-[200px] whitespace-pre-wrap">
            {detail.ocr_text}
          </pre>
        </details>
      )}

      {/* Notes */}
      {detail.notes && (
        <div className="mb-4">
          <h4 className="text-sm font-medium text-text-secondary mb-1">Notes</h4>
          <div className="text-sm">{detail.notes}</div>
        </div>
      )}

      {/* Meta */}
      <div className="text-xs text-text-secondary space-y-1 mb-4">
        <div>Uploaded: {new Date(detail.uploaded_at).toLocaleString()}</div>
        {detail.uploaded_by && <div>By: {detail.uploaded_by}</div>}
        {detail.match_confidence != null && (
          <div>Match confidence: {(detail.match_confidence * 100).toFixed(0)}%</div>
        )}
      </div>

      {/* Actions */}
      <button
        onClick={onDelete}
        className="w-full px-3 py-2 text-sm text-expense border border-expense/30 rounded hover:bg-expense/10 transition-colors"
      >
        Delete Receipt
      </button>
    </div>
  )
}
