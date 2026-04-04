import { apiFetch } from './client'

export interface SwExpense {
  id: number
  date: string
  cost: string
  currency_code: string
  description: string
  group_id: number
  group_name: string | null
  status: 'pending' | 'ignored'
}

export interface SwCandidate {
  id: string
  date: string
  amount: string
  currency: string
  merchant: string
  institution: string
  already_linked: boolean
  matched_via?: string
}

export interface SwExpenseDetail {
  id: number
  date: string
  cost: string
  currency_code: string
  description: string
  details: string | null
  original_currency: string | null
}

export interface SwCandidatesResponse {
  expense: SwExpenseDetail
  candidates: SwCandidate[]
}

export interface OutgoingTransaction {
  id: string
  date: string
  amount: string
  currency: string
  raw_merchant: string
  merchant_name: string
  category_path: string | null
  institution: string
  note: string | null
}

export interface SwMember {
  id: number
  name: string
}

export interface SwGroup {
  id: number
  name: string
  members: SwMember[]
  created_at: string
}

export interface SwGroupsResponse {
  user_id: number
  groups: SwGroup[]
}

export function fetchSwIncoming(opts: {
  since?: string
  showAll?: boolean
  showIgnored?: boolean
} = {}) {
  const params = new URLSearchParams()
  if (opts.since) params.set('since', opts.since)
  if (opts.showAll) params.set('show_all', 'true')
  if (opts.showIgnored) params.set('show_ignored', 'true')
  return apiFetch<SwExpense[]>(`/splitwise/incoming?${params}`)
}

export function fetchSwCandidates(expenseId: number) {
  return apiFetch<SwCandidatesResponse>(`/splitwise/incoming/${expenseId}/candidates`)
}

export function linkSwExpense(expenseId: number, transactionId: string) {
  return apiFetch<{ ok: boolean }>(`/splitwise/incoming/${expenseId}/link`, {
    method: 'POST',
    body: JSON.stringify({ transaction_id: transactionId }),
  })
}

export function ignoreSwExpense(expenseId: number) {
  return apiFetch<{ ok: boolean }>(`/splitwise/incoming/${expenseId}/ignore`, {
    method: 'POST',
  })
}

export function unignoreSwExpense(expenseId: number) {
  return apiFetch<{ ok: boolean }>(`/splitwise/incoming/${expenseId}/ignore`, {
    method: 'DELETE',
  })
}

export function fetchSwOutgoing() {
  return apiFetch<OutgoingTransaction[]>('/splitwise/outgoing')
}

export function fetchSwGroups() {
  return apiFetch<SwGroupsResponse>('/splitwise/groups')
}

export function pushSwExpense(transactionId: string, groupId: number, memberIds: number[]) {
  return apiFetch<{ ok: boolean; splitwise_expense_id: number }>(
    `/splitwise/outgoing/${transactionId}/push`,
    {
      method: 'POST',
      body: JSON.stringify({ group_id: groupId, member_ids: memberIds }),
    },
  )
}
