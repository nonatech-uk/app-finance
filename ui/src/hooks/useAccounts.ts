import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { fetchAccounts, fetchAccountDetail, fetchFavouriteAccounts, updateAccount } from '../api/accounts'
import type { AccountUpdate } from '../api/types'

export function useAccounts(includeArchived = false, scope?: string) {
  return useQuery({
    queryKey: ['accounts', { includeArchived, scope }],
    queryFn: () => fetchAccounts(includeArchived, scope),
    staleTime: 5 * 60 * 1000,
  })
}

export function useAccountDetail(institution: string, accountRef: string, scope?: string) {
  return useQuery({
    queryKey: ['account', institution, accountRef, scope],
    queryFn: () => fetchAccountDetail(institution, accountRef, scope),
    enabled: !!institution && !!accountRef,
  })
}

export function useFavouriteAccounts(scope?: string) {
  return useQuery({
    queryKey: ['accounts', 'favourites', scope],
    queryFn: () => fetchFavouriteAccounts(scope),
    staleTime: 5 * 60 * 1000,
  })
}

export function useUpdateAccount() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ institution, accountRef, body }: {
      institution: string
      accountRef: string
      body: AccountUpdate
    }) => updateAccount(institution, accountRef, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['accounts'] })
    },
  })
}
