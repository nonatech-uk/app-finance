import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  fetchPortfolio,
  fetchHoldings,
  fetchHoldingDetail,
  createHolding,
  createTrade,
  deleteTrade,
  fetchCgt,
  fetchTaxYears,
  updateTaxYear,
  refreshPrices,
} from '../api/stocks'

export function usePortfolio(scope?: string) {
  return useQuery({
    queryKey: ['stocks', 'portfolio', scope],
    queryFn: () => fetchPortfolio(scope),
    staleTime: 2 * 60 * 1000,
  })
}

export function useHoldings(scope?: string) {
  return useQuery({
    queryKey: ['stocks', 'holdings', scope],
    queryFn: () => fetchHoldings(scope),
    staleTime: 5 * 60 * 1000,
  })
}

export function useHoldingDetail(id: string) {
  return useQuery({
    queryKey: ['stocks', 'holding', id],
    queryFn: () => fetchHoldingDetail(id),
    enabled: !!id,
  })
}

export function useCreateHolding() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: createHolding,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['stocks'] }),
  })
}

export function useCreateTrade() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ holdingId, ...data }: { holdingId: string; trade_type: string; trade_date: string; quantity: string; price_per_share?: string; fees?: string; gbp_total_cost?: string; notes?: string }) =>
      createTrade(holdingId, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['stocks'] }),
  })
}

export function useDeleteTrade() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: deleteTrade,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['stocks'] }),
  })
}

export function useCgt(taxYear?: string, qtyOverrides?: Record<string, string>) {
  return useQuery({
    queryKey: ['stocks', 'cgt', taxYear, qtyOverrides],
    queryFn: () => fetchCgt(taxYear, qtyOverrides),
    staleTime: 5 * 60 * 1000,
  })
}

export function useTaxYears() {
  return useQuery({
    queryKey: ['stocks', 'tax-years'],
    queryFn: fetchTaxYears,
    staleTime: 30 * 60 * 1000,
  })
}

export function useUpdateTaxYear() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ taxYear, ...data }: { taxYear: string; gross_income: string; personal_allowance?: string; notes?: string }) =>
      updateTaxYear(taxYear, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['stocks'] }),
  })
}

export function useRefreshPrices() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: refreshPrices,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['stocks'] }),
  })
}
