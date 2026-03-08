import { apiFetch } from './client'
import type { AccountList, AccountDetailResponse, AccountUpdate } from './types'

export function fetchAccounts(includeArchived = false, scope?: string) {
  const params = new URLSearchParams()
  if (includeArchived) params.set('include_archived', 'true')
  if (scope) params.set('scope', scope)
  const qs = params.toString()
  return apiFetch<AccountList>(`/accounts${qs ? '?' + qs : ''}`)
}

export function fetchAccountDetail(institution: string, accountRef: string, scope?: string) {
  const qs = scope ? `?scope=${scope}` : ''
  return apiFetch<AccountDetailResponse>(`/accounts/${institution}/${accountRef}${qs}`)
}

export function fetchFavouriteAccounts(scope?: string) {
  const qs = scope ? `?scope=${scope}` : ''
  return apiFetch<AccountList>(`/accounts/favourites${qs}`)
}

export function updateAccount(institution: string, accountRef: string, body: AccountUpdate) {
  return apiFetch<Record<string, unknown>>(`/accounts/${institution}/${accountRef}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}
