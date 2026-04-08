import { apiFetch } from './client'
import type { TransactionList, TransactionDetail, BulkOperationResult, BulkMerchantNameResult, BulkTagReplaceResult } from './types'

export interface TransactionFilters {
  cursor?: string
  offset?: number
  limit?: number
  sort_by?: string
  sort_dir?: 'asc' | 'desc'
  institution?: string
  account_ref?: string
  source?: string
  category?: string
  date_from?: string
  date_to?: string
  amount_min?: number
  amount_max?: number
  currency?: string
  search?: string
  tag?: string
  tag_pattern?: string
  uncategorised?: boolean
  scope?: string
}

export function fetchTransactions(filters: TransactionFilters = {}) {
  const params = new URLSearchParams()
  Object.entries(filters).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') params.set(k, String(v))
  })
  const qs = params.toString()
  return apiFetch<TransactionList>(`/transactions${qs ? '?' + qs : ''}`)
}

export function fetchTransaction(id: string) {
  return apiFetch<TransactionDetail>(`/transactions/${id}`)
}

export function updateTransactionNote(id: string, note: string) {
  return apiFetch<{ ok: boolean }>(`/transactions/${id}/note`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ note }),
  })
}

export function updateTransactionCategory(id: string, categoryPath: string) {
  return apiFetch<{ ok: boolean }>(`/transactions/${id}/category`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ category_path: categoryPath }),
  })
}

export function linkTransfer(id: string, counterpartId: string) {
  return apiFetch<{ ok: boolean; event_id: string }>(`/transactions/${id}/link-transfer`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ counterpart_id: counterpartId }),
  })
}

export function unlinkEvent(eventId: string) {
  return apiFetch<{ ok: boolean }>(`/economic-events/${eventId}`, {
    method: 'DELETE',
  })
}

export function addTransactionTag(id: string, tag: string) {
  return apiFetch<{ ok: boolean }>(`/transactions/${id}/tags`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tag }),
  })
}

export function removeTransactionTag(id: string, tagName: string) {
  return apiFetch<{ ok: boolean }>(
    `/transactions/${id}/tags/${encodeURIComponent(tagName)}`,
    { method: 'DELETE' },
  )
}

export function fetchAllTags() {
  return apiFetch<{ items: { tag: string; count: number }[] }>('/tags')
}

// ── Bulk Operations ──

export function bulkUpdateCategory(transactionIds: string[], categoryPath: string) {
  return apiFetch<BulkOperationResult>('/transactions/bulk/category', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ transaction_ids: transactionIds, category_path: categoryPath }),
  })
}

export function bulkUpdateMerchantName(transactionIds: string[], displayName: string | null) {
  return apiFetch<BulkMerchantNameResult>('/transactions/bulk/merchant-name', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ transaction_ids: transactionIds, display_name: displayName }),
  })
}

export function bulkAddTags(transactionIds: string[], tags: string[]) {
  return apiFetch<BulkOperationResult>('/transactions/bulk/tags/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ transaction_ids: transactionIds, tags }),
  })
}

export function bulkRemoveTag(transactionIds: string[], tag: string) {
  return apiFetch<BulkOperationResult>('/transactions/bulk/tags/remove', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ transaction_ids: transactionIds, tag }),
  })
}

export function bulkReplaceTags(transactionIds: string[], tags: string[]) {
  return apiFetch<BulkTagReplaceResult>('/transactions/bulk/tags/replace', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ transaction_ids: transactionIds, tags }),
  })
}

export function bulkUpdateNote(transactionIds: string[], note: string, mode: 'replace' | 'append') {
  return apiFetch<BulkOperationResult>('/transactions/bulk/note', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ transaction_ids: transactionIds, note, mode }),
  })
}

// ── Split Transactions ──

export interface SplitLineInput {
  amount: number
  category_path?: string | null
  description?: string | null
}

export function saveSplit(id: string, lines: SplitLineInput[]) {
  return apiFetch<{ ok: boolean; lines: unknown[] }>(`/transactions/${id}/split`, {
    method: 'PUT',
    body: JSON.stringify({ lines }),
  })
}

export function deleteSplit(id: string) {
  return apiFetch<{ ok: boolean }>(`/transactions/${id}/split`, {
    method: 'DELETE',
  })
}

export interface AmazonSplitSuggestion {
  lines: { amount: string; description: string; category_path: string | null }[]
  order_ids: string[]
}

export function suggestAmazonSplit(id: string) {
  return apiFetch<AmazonSplitSuggestion>(`/transactions/${id}/split/suggest-amazon`)
}

// ── PayPal ──

import type { PayPalTransaction, PayPalMatchItem } from './types'

export function searchPayPal(params: { q?: string; type?: string }): Promise<PayPalTransaction[]> {
  const qs = new URLSearchParams()
  if (params.q) qs.set('q', params.q)
  if (params.type) qs.set('type', params.type)
  return apiFetch(`/paypal/search?${qs}`)
}

export function matchPayPal(data: { paypal_transaction_id: string; raw_transaction_id: string }): Promise<PayPalMatchItem> {
  return apiFetch(`/paypal/match`, {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export function unmatchPayPal(matchId: string): Promise<void> {
  return apiFetch(`/paypal/match/${matchId}`, { method: 'DELETE' })
}
