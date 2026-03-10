import { useMutation, useQueryClient } from '@tanstack/react-query'
import { uploadCsvPreview, confirmCsvImport, bankivityPreview, bankivityConfirm } from '../api/imports'

export function useCsvPreview() {
  return useMutation({
    mutationFn: ({
      file,
      institution,
      accountRef,
    }: {
      file: File
      institution: string
      accountRef: string
    }) => uploadCsvPreview(file, institution, accountRef),
  })
}

export function useCsvConfirm() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      institution,
      accountRef,
    }: {
      institution: string
      accountRef: string
    }) => confirmCsvImport(institution, accountRef),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['transactions'] })
      queryClient.invalidateQueries({ queryKey: ['accounts'] })
      queryClient.invalidateQueries({ queryKey: ['stats'] })
    },
  })
}

export function useBankivityPreview() {
  return useMutation({
    mutationFn: (file: File) => bankivityPreview(file),
  })
}

export function useBankivityConfirm() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => bankivityConfirm(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['transactions'] })
      queryClient.invalidateQueries({ queryKey: ['accounts'] })
      queryClient.invalidateQueries({ queryKey: ['stats'] })
    },
  })
}
