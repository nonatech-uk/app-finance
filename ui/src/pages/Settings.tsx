import { useEffect, useState } from 'react'
import { useSettings, useUpdateSettings } from '../hooks/useSettings'
import { useBankivityPreview, useBankivityConfirm } from '../hooks/useImports'
import type { BankivityPreviewResult, BankivityImportResult } from '../api/types'
import LoadingSpinner from '../components/common/LoadingSpinner'

export default function Settings() {
  const { data, isLoading } = useSettings()
  const updateSettings = useUpdateSettings()

  const [caldavEnabled, setCaldavEnabled] = useState(true)
  const [caldavTag, setCaldavTag] = useState('todo')
  const [caldavPassword, setCaldavPassword] = useState('')
  const [passwordDirty, setPasswordDirty] = useState(false)
  const [saved, setSaved] = useState(false)

  // Receipt settings
  const [receiptAlertDays, setReceiptAlertDays] = useState(7)
  const [receiptDateTolerance, setReceiptDateTolerance] = useState(2)
  const [receiptAutoMatch, setReceiptAutoMatch] = useState(true)
  const [receiptAmountTolerancePct, setReceiptAmountTolerancePct] = useState(20)
  const [anthropicKey, setAnthropicKey] = useState('')
  const [anthropicKeyDirty, setAnthropicKeyDirty] = useState(false)

  // Webhook settings
  const [webhookEnabled, setWebhookEnabled] = useState(false)
  const [webhookAllowedSenders, setWebhookAllowedSenders] = useState('')
  const [copied, setCopied] = useState(false)

  // Bankivity import
  const [bank8Path, setBank8Path] = useState('')
  const [bankPreviewResult, setBankPreviewResult] = useState<BankivityPreviewResult | null>(null)
  const [bankImportResult, setBankImportResult] = useState<BankivityImportResult | null>(null)
  const bankPreview = useBankivityPreview()
  const bankConfirm = useBankivityConfirm()

  // Sync form state when data loads
  useEffect(() => {
    if (data) {
      setCaldavEnabled(data.caldav_enabled)
      setCaldavTag(data.caldav_tag)
      setCaldavPassword('')
      setPasswordDirty(false)
      setReceiptAlertDays(data.receipt_alert_days)
      setReceiptDateTolerance(data.receipt_match_date_tolerance)
      setReceiptAutoMatch(data.receipt_auto_match_enabled)
      setReceiptAmountTolerancePct(data.receipt_amount_tolerance_pct)
      setAnthropicKey('')
      setAnthropicKeyDirty(false)
      setWebhookEnabled(data.webhook_receipt_enabled)
      setWebhookAllowedSenders(data.webhook_receipt_allowed_senders)
      if (data.bankivity_last_path) setBank8Path(data.bankivity_last_path)
    }
  }, [data])

  if (isLoading) return <LoadingSpinner />

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const body: Record<string, unknown> = {
      caldav_enabled: caldavEnabled,
      caldav_tag: caldavTag,
    }
    if (passwordDirty) {
      body.caldav_password = caldavPassword
    }
    updateSettings.mutate(body, {
      onSuccess: () => {
        setSaved(true)
        setPasswordDirty(false)
        setTimeout(() => setSaved(false), 2000)
      },
    })
  }

  const handleReceiptSave = (e: React.FormEvent) => {
    e.preventDefault()
    const body: Record<string, unknown> = {
      receipt_alert_days: receiptAlertDays,
      receipt_match_date_tolerance: receiptDateTolerance,
      receipt_auto_match_enabled: receiptAutoMatch,
      receipt_amount_tolerance_pct: receiptAmountTolerancePct,
    }
    if (anthropicKeyDirty) {
      body.anthropic_api_key = anthropicKey
    }
    updateSettings.mutate(body, {
      onSuccess: () => {
        setSaved(true)
        setAnthropicKeyDirty(false)
        setTimeout(() => setSaved(false), 2000)
      },
    })
  }

  const handleWebhookSave = (e: React.FormEvent) => {
    e.preventDefault()
    const body: Record<string, unknown> = {
      webhook_receipt_enabled: webhookEnabled,
      webhook_receipt_allowed_senders: webhookAllowedSenders,
    }
    // If no secret exists yet, send empty string to trigger auto-generation
    if (!data?.webhook_receipt_secret) {
      body.webhook_receipt_secret = ''
    }
    updateSettings.mutate(body, {
      onSuccess: () => {
        setSaved(true)
        setTimeout(() => setSaved(false), 2000)
      },
    })
  }

  const webhookUrl = data?.webhook_receipt_secret
    ? `${window.location.origin}/api/v1/receipts/webhook?token=${data.webhook_receipt_secret}`
    : ''

  const serverUrl = `${window.location.origin}/caldav/`

  const willHavePassword = passwordDirty ? caldavPassword.length > 0 : !!data?.caldav_password_set
  const canEnable = willHavePassword

  return (
    <div className="space-y-6 max-w-2xl">
      <h2 className="text-xl font-semibold text-text-primary">Settings</h2>

      {/* Bankivity Import */}
      <div className="bg-bg-card border border-border rounded-lg p-5 space-y-5">
        <div>
          <h3 className="text-base font-medium text-text-primary">Bankivity Import</h3>
          <p className="text-sm text-text-secondary mt-1">
            Import First Direct transactions from Bankivity's Salt Edge connection.
          </p>
        </div>

        <div className="space-y-4">
          <div>
            <label className="block text-sm text-text-secondary mb-1">.bank8 file path</label>
            <input
              type="text"
              value={bank8Path}
              onChange={e => {
                setBank8Path(e.target.value)
                setBankPreviewResult(null)
                setBankImportResult(null)
              }}
              placeholder="/path/to/NonaFinance.bank8"
              className="w-full px-3 py-1.5 text-sm bg-bg-secondary border border-border rounded-md text-text-primary placeholder:text-text-secondary/50 focus:outline-none focus:ring-1 focus:ring-accent font-mono"
            />
          </div>

          {/* Preview results */}
          {bankPreviewResult && !bankImportResult && (
            <div className="text-sm text-text-primary space-y-1">
              <p>
                Found {bankPreviewResult.total} transactions
                {' '}({bankPreviewResult.new_count} new,{' '}
                {bankPreviewResult.existing_count} existing)
              </p>
              {Object.entries(bankPreviewResult.by_account).map(([acct, count]) => (
                <p key={acct} className="text-text-secondary pl-3">
                  {acct}: {count} new
                </p>
              ))}
            </div>
          )}

          {/* Import result */}
          {bankImportResult && (
            <div className="text-sm space-y-1">
              <p className="text-green-400">
                Imported {bankImportResult.inserted} transactions
                {bankImportResult.skipped > 0 && ` (${bankImportResult.skipped} skipped)`}
              </p>
            </div>
          )}

          {/* Error display */}
          {(bankPreview.isError || bankConfirm.isError) && (
            <p className="text-sm text-red-400">
              {(bankPreview.error as Error)?.message || (bankConfirm.error as Error)?.message}
            </p>
          )}

          {/* Action buttons */}
          <div className="flex items-center gap-3">
            {!bankPreviewResult ? (
              <button
                type="button"
                disabled={!bank8Path.trim() || bankPreview.isPending}
                onClick={() => {
                  setBankImportResult(null)
                  bankPreview.mutate(bank8Path.trim(), {
                    onSuccess: (result) => setBankPreviewResult(result),
                  })
                }}
                className="px-4 py-1.5 text-sm font-medium rounded-md bg-accent text-white hover:bg-accent/90 disabled:opacity-50 transition-colors"
              >
                {bankPreview.isPending ? 'Reading...' : 'Preview Import'}
              </button>
            ) : !bankImportResult ? (
              <>
                <button
                  type="button"
                  disabled={bankConfirm.isPending || bankPreviewResult.new_count === 0}
                  onClick={() => {
                    bankConfirm.mutate(bank8Path.trim(), {
                      onSuccess: (result) => {
                        setBankImportResult(result)
                        setBankPreviewResult(null)
                      },
                    })
                  }}
                  className="px-4 py-1.5 text-sm font-medium rounded-md bg-accent text-white hover:bg-accent/90 disabled:opacity-50 transition-colors"
                >
                  {bankConfirm.isPending
                    ? 'Importing...'
                    : `Import ${bankPreviewResult.new_count} Transaction${bankPreviewResult.new_count !== 1 ? 's' : ''}`}
                </button>
                <button
                  type="button"
                  onClick={() => setBankPreviewResult(null)}
                  className="px-4 py-1.5 text-sm font-medium rounded-md bg-bg-secondary border border-border text-text-primary hover:bg-bg-hover transition-colors"
                >
                  Cancel
                </button>
              </>
            ) : (
              <button
                type="button"
                onClick={() => {
                  setBankImportResult(null)
                  setBankPreviewResult(null)
                }}
                className="px-4 py-1.5 text-sm font-medium rounded-md bg-bg-secondary border border-border text-text-primary hover:bg-bg-hover transition-colors"
              >
                Done
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Receipt Management */}
      <div className="bg-bg-card border border-border rounded-lg p-5 space-y-5">
        <div>
          <h3 className="text-base font-medium text-text-primary">Receipt Management</h3>
          <p className="text-sm text-text-secondary mt-1">
            OCR, auto-matching, and alert settings for uploaded receipts.
          </p>
        </div>

        <form onSubmit={handleReceiptSave} className="space-y-4">
          {/* Anthropic API Key */}
          <div>
            <label className="block text-sm text-text-secondary mb-1">Anthropic API Key (for OCR)</label>
            <input
              type="password"
              value={anthropicKeyDirty ? anthropicKey : (data?.anthropic_api_key_set ? '••••••••••••••••' : '')}
              onChange={e => {
                setAnthropicKey(e.target.value)
                setAnthropicKeyDirty(true)
              }}
              onFocus={() => {
                if (!anthropicKeyDirty) {
                  setAnthropicKey('')
                  setAnthropicKeyDirty(true)
                }
              }}
              placeholder={data?.anthropic_api_key_set ? 'Key is set' : 'sk-ant-...'}
              className="w-full px-3 py-1.5 text-sm bg-bg-secondary border border-border rounded-md text-text-primary placeholder:text-text-secondary/50 focus:outline-none focus:ring-1 focus:ring-accent font-mono"
            />
            <p className="text-xs text-text-secondary mt-1">
              {data?.anthropic_api_key_set
                ? 'API key is configured. Enter a new value to change it.'
                : 'Required for receipt OCR. Get a key from console.anthropic.com.'}
            </p>
          </div>

          {/* Auto-match toggle */}
          <label className="flex items-center gap-3 cursor-pointer">
            <button
              type="button"
              role="switch"
              aria-checked={receiptAutoMatch}
              onClick={() => setReceiptAutoMatch(!receiptAutoMatch)}
              className={`relative inline-flex h-6 w-11 shrink-0 rounded-full transition-colors ${
                receiptAutoMatch ? 'bg-accent' : 'bg-bg-hover'
              }`}
            >
              <span
                className={`inline-block h-5 w-5 rounded-full bg-white shadow transform transition-transform mt-0.5 ${
                  receiptAutoMatch ? 'translate-x-[22px]' : 'translate-x-0.5'
                }`}
              />
            </button>
            <span className="text-sm text-text-primary">Auto-match receipts to transactions</span>
          </label>

          {/* Date tolerance */}
          <div>
            <label className="block text-sm text-text-secondary mb-1">Date tolerance (days)</label>
            <input
              type="number"
              min={0}
              max={14}
              value={receiptDateTolerance}
              onChange={e => setReceiptDateTolerance(parseInt(e.target.value) || 0)}
              className="w-24 px-3 py-1.5 text-sm bg-bg-secondary border border-border rounded-md text-text-primary focus:outline-none focus:ring-1 focus:ring-accent"
            />
            <p className="text-xs text-text-secondary mt-1">
              How many days either side of the receipt date to search for matching transactions.
            </p>
          </div>

          {/* Amount tolerance */}
          <div>
            <label className="block text-sm text-text-secondary mb-1">Amount tolerance (%)</label>
            <input
              type="number"
              min={0}
              max={100}
              value={receiptAmountTolerancePct}
              onChange={e => setReceiptAmountTolerancePct(parseInt(e.target.value) || 0)}
              className="w-24 px-3 py-1.5 text-sm bg-bg-secondary border border-border rounded-md text-text-primary focus:outline-none focus:ring-1 focus:ring-accent"
            />
            <p className="text-xs text-text-secondary mt-1">
              Allow matching when amounts differ by up to this percentage (e.g. tips). 0 = exact match only.
            </p>
          </div>

          {/* Alert days */}
          <div>
            <label className="block text-sm text-text-secondary mb-1">Alert after (days unmatched)</label>
            <input
              type="number"
              min={1}
              max={90}
              value={receiptAlertDays}
              onChange={e => setReceiptAlertDays(parseInt(e.target.value) || 7)}
              className="w-24 px-3 py-1.5 text-sm bg-bg-secondary border border-border rounded-md text-text-primary focus:outline-none focus:ring-1 focus:ring-accent"
            />
            <p className="text-xs text-text-secondary mt-1">
              Create a CalDAV alert if a receipt remains unmatched for this many days.
            </p>
          </div>

          {/* Save */}
          <div className="flex items-center gap-3 pt-1">
            <button
              type="submit"
              disabled={updateSettings.isPending}
              className="px-4 py-1.5 text-sm font-medium rounded-md bg-accent text-white hover:bg-accent/90 disabled:opacity-50 transition-colors"
            >
              {updateSettings.isPending ? 'Saving...' : 'Save'}
            </button>
            {saved && (
              <span className="text-sm text-green-400">Saved ✓</span>
            )}
            {updateSettings.isError && (
              <span className="text-sm text-red-400">
                Error: {updateSettings.error?.message || 'Failed to save'}
              </span>
            )}
          </div>
        </form>
      </div>

      {/* Email Webhook */}
      <div className="bg-bg-card border border-border rounded-lg p-5 space-y-5">
        <div>
          <h3 className="text-base font-medium text-text-primary">Email Webhook</h3>
          <p className="text-sm text-text-secondary mt-1">
            Forward receipt emails to automatically ingest attachments and text receipts.
          </p>
        </div>

        <form onSubmit={handleWebhookSave} className="space-y-4">
          {/* Enable toggle */}
          <label className="flex items-center gap-3 cursor-pointer">
            <button
              type="button"
              role="switch"
              aria-checked={webhookEnabled}
              onClick={() => setWebhookEnabled(!webhookEnabled)}
              className={`relative inline-flex h-6 w-11 shrink-0 rounded-full transition-colors ${
                webhookEnabled ? 'bg-accent' : 'bg-bg-hover'
              }`}
            >
              <span
                className={`inline-block h-5 w-5 rounded-full bg-white shadow transform transition-transform mt-0.5 ${
                  webhookEnabled ? 'translate-x-[22px]' : 'translate-x-0.5'
                }`}
              />
            </button>
            <span className="text-sm text-text-primary">Accept incoming receipt emails</span>
          </label>

          {/* Webhook URL */}
          {webhookUrl ? (
            <div>
              <label className="block text-sm text-text-secondary mb-1">Webhook URL</label>
              <div className="flex gap-2">
                <code className="flex-1 px-3 py-1.5 text-xs bg-bg-secondary border border-border rounded-md text-text-primary select-all break-all font-mono">
                  {webhookUrl}
                </code>
                <button
                  type="button"
                  onClick={() => {
                    navigator.clipboard.writeText(webhookUrl)
                    setCopied(true)
                    setTimeout(() => setCopied(false), 2000)
                  }}
                  className="px-3 py-1.5 text-xs font-medium rounded-md bg-bg-secondary border border-border text-text-primary hover:bg-bg-hover transition-colors shrink-0"
                >
                  {copied ? 'Copied!' : 'Copy'}
                </button>
              </div>
              <p className="text-xs text-text-secondary mt-1">
                Add this URL as a webhook destination in your ForwardEmail domain settings.
              </p>
            </div>
          ) : (
            <p className="text-xs text-amber-400">
              Save to generate a webhook URL with authentication token.
            </p>
          )}

          {/* Allowed senders */}
          <div>
            <label className="block text-sm text-text-secondary mb-1">Allowed senders (optional)</label>
            <textarea
              value={webhookAllowedSenders}
              onChange={e => setWebhookAllowedSenders(e.target.value)}
              placeholder="one email per line"
              rows={3}
              className="w-full px-3 py-1.5 text-sm bg-bg-secondary border border-border rounded-md text-text-primary placeholder:text-text-secondary/50 focus:outline-none focus:ring-1 focus:ring-accent font-mono"
            />
            <p className="text-xs text-text-secondary mt-1">
              Only accept emails from these addresses. Leave empty to accept from anyone.
            </p>
          </div>

          {/* Save */}
          <div className="flex items-center gap-3 pt-1">
            <button
              type="submit"
              disabled={updateSettings.isPending}
              className="px-4 py-1.5 text-sm font-medium rounded-md bg-accent text-white hover:bg-accent/90 disabled:opacity-50 transition-colors"
            >
              {updateSettings.isPending ? 'Saving...' : 'Save'}
            </button>
            {saved && (
              <span className="text-sm text-green-400">Saved ✓</span>
            )}
          </div>
        </form>
      </div>

      {/* CalDAV Task Feed */}
      <div className="bg-bg-card border border-border rounded-lg p-5 space-y-5">
        <div>
          <h3 className="text-base font-medium text-text-primary">CalDAV Task Feed</h3>
          <p className="text-sm text-text-secondary mt-1">
            Sync tagged transactions to Apple Reminders or other CalDAV clients.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Enable/Disable */}
          <label className="flex items-center gap-3 cursor-pointer">
            <button
              type="button"
              role="switch"
              aria-checked={caldavEnabled}
              onClick={() => {
                if (!caldavEnabled && !canEnable) return
                setCaldavEnabled(!caldavEnabled)
              }}
              className={`relative inline-flex h-6 w-11 shrink-0 rounded-full transition-colors ${
                caldavEnabled ? 'bg-accent' : 'bg-bg-hover'
              } ${!caldavEnabled && !canEnable ? 'opacity-50 cursor-not-allowed' : ''}`}
            >
              <span
                className={`inline-block h-5 w-5 rounded-full bg-white shadow transform transition-transform mt-0.5 ${
                  caldavEnabled ? 'translate-x-[22px]' : 'translate-x-0.5'
                }`}
              />
            </button>
            <span className="text-sm text-text-primary">Enable task feed</span>
          </label>
          {!canEnable && !caldavEnabled && (
            <p className="text-xs text-amber-400 -mt-2">Set an app password below to enable the feed.</p>
          )}

          {/* Tag Name */}
          <div>
            <label className="block text-sm text-text-secondary mb-1">Tag name</label>
            <input
              type="text"
              value={caldavTag}
              onChange={e => setCaldavTag(e.target.value)}
              placeholder="todo"
              className="w-48 px-3 py-1.5 text-sm bg-bg-secondary border border-border rounded-md text-text-primary placeholder:text-text-secondary/50 focus:outline-none focus:ring-1 focus:ring-accent"
            />
            <p className="text-xs text-text-secondary mt-1">
              Transactions with this tag appear as tasks in your CalDAV client.
            </p>
          </div>

          {/* App Password */}
          <div>
            <label className="block text-sm text-text-secondary mb-1">App password</label>
            <input
              type="password"
              value={passwordDirty ? caldavPassword : (data?.caldav_password_set ? '••••••••' : '')}
              onChange={e => {
                setCaldavPassword(e.target.value)
                setPasswordDirty(true)
                if (!e.target.value && caldavEnabled) {
                  setCaldavEnabled(false)
                }
              }}
              onFocus={() => {
                if (!passwordDirty) {
                  setCaldavPassword('')
                  setPasswordDirty(true)
                }
              }}
              placeholder={data?.caldav_password_set ? '••••••••' : 'No password set'}
              className="w-64 px-3 py-1.5 text-sm bg-bg-secondary border border-border rounded-md text-text-primary placeholder:text-text-secondary/50 focus:outline-none focus:ring-1 focus:ring-accent"
            />
            <p className="text-xs text-text-secondary mt-1">
              {data?.caldav_password_set
                ? 'Password is set. Enter a new value to change it.'
                : 'An app password is required to enable the CalDAV feed.'}
            </p>
          </div>

          {/* Save */}
          <div className="flex items-center gap-3 pt-1">
            <button
              type="submit"
              disabled={updateSettings.isPending}
              className="px-4 py-1.5 text-sm font-medium rounded-md bg-accent text-white hover:bg-accent/90 disabled:opacity-50 transition-colors"
            >
              {updateSettings.isPending ? 'Saving...' : 'Save'}
            </button>
            {saved && (
              <span className="text-sm text-green-400">Saved ✓</span>
            )}
            {updateSettings.isError && (
              <span className="text-sm text-red-400">
                Error: {updateSettings.error?.message || 'Failed to save'}
              </span>
            )}
          </div>
        </form>

        {/* Connection Info */}
        <div className="border-t border-border pt-4 mt-4 space-y-2">
          <h4 className="text-sm font-medium text-text-primary">Connection details</h4>
          <div className="text-xs text-text-secondary space-y-1.5">
            <div>
              <span className="text-text-secondary/70">Server URL: </span>
              <code className="bg-bg-secondary px-1.5 py-0.5 rounded text-text-primary select-all">{serverUrl}</code>
            </div>
            <div>
              <span className="text-text-secondary/70">Username: </span>
              <span className="text-text-primary">anything (ignored)</span>
            </div>
            <div>
              <span className="text-text-secondary/70">Password: </span>
              <span className="text-text-primary">the app password above</span>
            </div>
            <p className="text-text-secondary/70 pt-1">
              In Apple Reminders: add an "Other CalDAV Account" with these details.
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
