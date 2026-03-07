import { useEffect, useState } from 'react'
import { useSettings, useUpdateSettings } from '../hooks/useSettings'
import LoadingSpinner from '../components/common/LoadingSpinner'

export default function Settings() {
  const { data, isLoading } = useSettings()
  const updateSettings = useUpdateSettings()

  const [caldavEnabled, setCaldavEnabled] = useState(true)
  const [caldavTag, setCaldavTag] = useState('todo')
  const [caldavPassword, setCaldavPassword] = useState('')
  const [passwordDirty, setPasswordDirty] = useState(false)
  const [saved, setSaved] = useState(false)

  // Sync form state when data loads
  useEffect(() => {
    if (data) {
      setCaldavEnabled(data.caldav_enabled)
      setCaldavTag(data.caldav_tag)
      setCaldavPassword('')
      setPasswordDirty(false)
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

  const serverUrl = `${window.location.origin}/caldav/`

  // Password will be set after save if: already set on server, or user has typed a non-empty value
  const willHavePassword = passwordDirty ? caldavPassword.length > 0 : !!data?.caldav_password_set
  // Can't enable without a password
  const canEnable = willHavePassword

  return (
    <div className="space-y-6 max-w-2xl">
      <h2 className="text-xl font-semibold text-text-primary">Settings</h2>

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
                // If clearing password, auto-disable the feed
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
              {updateSettings.isPending ? 'Saving…' : 'Save'}
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
