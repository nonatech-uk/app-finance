import { apiFetch } from './client'
import { ApiError } from './client'

const BASE_URL = '/api/v1'

export interface ReceiptItem {
  id: string
  original_filename: string
  mime_type: string
  file_size: number
  ocr_status: string
  extracted_date: string | null
  extracted_amount: number | null
  extracted_currency: string | null
  extracted_merchant: string | null
  match_status: string
  matched_transaction_id: string | null
  match_confidence: number | null
  matched_at: string | null
  matched_by: string | null
  source: string
  uploaded_at: string
  uploaded_by: string | null
  notes: string | null
}

export interface ReceiptDetail extends ReceiptItem {
  ocr_text: string | null
  ocr_data: Record<string, unknown> | null
  file_path: string | null
  thumbnail_path: string | null
  matched_transaction: {
    id: string
    posted_at: string
    amount: string
    currency: string | null
    raw_merchant: string | null
    institution: string
    account_ref: string
  } | null
}

export interface ReceiptList {
  items: ReceiptItem[]
  total: number
}

export interface ReceiptCandidate {
  id: string
  posted_at: string
  amount: string
  currency: string | null
  raw_merchant: string | null
  institution: string
  account_ref: string
}

export async function uploadReceipt(file: File, note?: string): Promise<ReceiptDetail> {
  const form = new FormData()
  form.append('file', file)
  if (note) form.append('note', note)

  const res = await fetch(`${BASE_URL}/receipts/upload`, {
    method: 'POST',
    body: form,
    credentials: 'include',
  })
  if (!res.ok) {
    const body = await res.text()
    throw new ApiError(res.status, body)
  }
  return res.json()
}

export function fetchReceipts(status: string = 'all', limit: number = 50, offset: number = 0) {
  return apiFetch<ReceiptList>(`/receipts?status=${status}&limit=${limit}&offset=${offset}`)
}

export function fetchReceipt(id: string) {
  return apiFetch<ReceiptDetail>(`/receipts/${id}`)
}

export function matchReceipt(receiptId: string, transactionId: string) {
  return apiFetch<{ ok: boolean }>(`/receipts/${receiptId}/match`, {
    method: 'POST',
    body: JSON.stringify({ transaction_id: transactionId }),
  })
}

export function unmatchReceipt(receiptId: string) {
  return apiFetch<{ ok: boolean }>(`/receipts/${receiptId}/unmatch`, {
    method: 'POST',
  })
}

export function deleteReceipt(receiptId: string) {
  return apiFetch<{ ok: boolean }>(`/receipts/${receiptId}`, {
    method: 'DELETE',
  })
}

export function fetchCandidates(receiptId: string) {
  return apiFetch<{ candidates: ReceiptCandidate[] }>(`/receipts/${receiptId}/candidates`)
}

export function updateReceipt(receiptId: string, data: {
  extracted_date?: string | null
  extracted_amount?: number | null
  extracted_currency?: string | null
  extracted_merchant?: string | null
}) {
  return apiFetch<ReceiptDetail>(`/receipts/${receiptId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  })
}

export function fetchTransactionReceipts(transactionId: string) {
  return apiFetch<{ items: ReceiptItem[] }>(`/transactions/${transactionId}/receipts`)
}

export function receiptFileUrl(receiptId: string) {
  return `${BASE_URL}/receipts/${receiptId}/file`
}

export function receiptThumbnailUrl(receiptId: string) {
  return `${BASE_URL}/receipts/${receiptId}/thumbnail`
}
