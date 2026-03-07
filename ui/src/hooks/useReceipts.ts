import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  fetchReceipts,
  fetchReceipt,
  uploadReceipt,
  matchReceipt,
  unmatchReceipt,
  deleteReceipt,
  fetchCandidates,
  fetchTransactionReceipts,
} from '../api/receipts'

export function useReceipts(status: string = 'all', limit: number = 50, offset: number = 0) {
  return useQuery({
    queryKey: ['receipts', status, limit, offset],
    queryFn: () => fetchReceipts(status, limit, offset),
  })
}

export function useReceipt(id: string | null) {
  return useQuery({
    queryKey: ['receipt', id],
    queryFn: () => fetchReceipt(id!),
    enabled: !!id,
  })
}

export function useUploadReceipt() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ file, note }: { file: File; note?: string }) =>
      uploadReceipt(file, note),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['receipts'] })
    },
  })
}

export function useMatchReceipt() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ receiptId, transactionId }: { receiptId: string; transactionId: string }) =>
      matchReceipt(receiptId, transactionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['receipts'] })
      queryClient.invalidateQueries({ queryKey: ['receipt'] })
    },
  })
}

export function useUnmatchReceipt() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (receiptId: string) => unmatchReceipt(receiptId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['receipts'] })
      queryClient.invalidateQueries({ queryKey: ['receipt'] })
    },
  })
}

export function useDeleteReceipt() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (receiptId: string) => deleteReceipt(receiptId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['receipts'] })
    },
  })
}

export function useCandidates(receiptId: string | null) {
  return useQuery({
    queryKey: ['receipt-candidates', receiptId],
    queryFn: () => fetchCandidates(receiptId!),
    enabled: !!receiptId,
  })
}

export function useTransactionReceipts(transactionId: string | null) {
  return useQuery({
    queryKey: ['transaction-receipts', transactionId],
    queryFn: () => fetchTransactionReceipts(transactionId!),
    enabled: !!transactionId,
  })
}
