import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  fetchSwIncoming,
  fetchSwCandidates,
  linkSwExpense,
  ignoreSwExpense,
  unignoreSwExpense,
  fetchSwOutgoing,
  fetchSwGroups,
  pushSwExpense,
} from '../api/splitwise'

export function useSwIncoming(opts: {
  since?: string
  showAll?: boolean
  showIgnored?: boolean
} = {}) {
  return useQuery({
    queryKey: ['sw-incoming', opts],
    queryFn: () => fetchSwIncoming(opts),
  })
}

export function useSwCandidates(expenseId: number | null) {
  return useQuery({
    queryKey: ['sw-candidates', expenseId],
    queryFn: () => fetchSwCandidates(expenseId!),
    enabled: expenseId !== null,
  })
}

export function useLinkExpense() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ expenseId, transactionId }: { expenseId: number; transactionId: string }) =>
      linkSwExpense(expenseId, transactionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sw-incoming'] })
      queryClient.invalidateQueries({ queryKey: ['sw-candidates'] })
    },
  })
}

export function useIgnoreExpense() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (expenseId: number) => ignoreSwExpense(expenseId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sw-incoming'] })
    },
  })
}

export function useUnignoreExpense() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (expenseId: number) => unignoreSwExpense(expenseId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sw-incoming'] })
    },
  })
}

export function useSwOutgoing() {
  return useQuery({
    queryKey: ['sw-outgoing'],
    queryFn: fetchSwOutgoing,
  })
}

export function useSwGroups() {
  return useQuery({
    queryKey: ['sw-groups'],
    queryFn: fetchSwGroups,
    staleTime: 5 * 60_000,
  })
}

export function usePushExpense() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ transactionId, groupId, memberIds }: {
      transactionId: string
      groupId: number
      memberIds: number[]
    }) => pushSwExpense(transactionId, groupId, memberIds),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sw-outgoing'] })
    },
  })
}
