import { ApiError, apiFetch } from './client'
import type { CsvPreviewResult, CsvImportResult, BankivityPreviewResult, BankivityImportResult } from './types'

const BASE_URL = '/api/v1'

export async function uploadCsvPreview(
  file: File,
  institution: string,
  accountRef: string,
): Promise<CsvPreviewResult> {
  const form = new FormData()
  form.append('file', file)
  form.append('institution', institution)
  form.append('account_ref', accountRef)

  const res = await fetch(`${BASE_URL}/imports/csv/preview`, {
    method: 'POST',
    body: form,
  })
  if (!res.ok) {
    const body = await res.text()
    throw new ApiError(res.status, body)
  }
  return res.json()
}

export async function confirmCsvImport(
  institution: string,
  accountRef: string,
): Promise<CsvImportResult> {
  const form = new FormData()
  form.append('institution', institution)
  form.append('account_ref', accountRef)

  const res = await fetch(`${BASE_URL}/imports/csv/confirm`, {
    method: 'POST',
    body: form,
  })
  if (!res.ok) {
    const body = await res.text()
    throw new ApiError(res.status, body)
  }
  return res.json()
}

export async function bankivityPreview(bank8Path: string): Promise<BankivityPreviewResult> {
  return apiFetch<BankivityPreviewResult>('/imports/bankivity/preview', {
    method: 'POST',
    body: JSON.stringify({ bank8_path: bank8Path }),
  })
}

export async function bankivityConfirm(bank8Path: string): Promise<BankivityImportResult> {
  return apiFetch<BankivityImportResult>('/imports/bankivity/confirm', {
    method: 'POST',
    body: JSON.stringify({ bank8_path: bank8Path }),
  })
}
