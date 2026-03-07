import { apiFetch } from './client'
import type { SettingsResponse, SettingsUpdate } from './types'

export function fetchSettings() {
  return apiFetch<SettingsResponse>('/settings')
}

export function updateSettings(body: SettingsUpdate) {
  return apiFetch<SettingsResponse>('/settings', {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}
