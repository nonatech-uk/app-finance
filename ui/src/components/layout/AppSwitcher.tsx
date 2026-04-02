import { useState, useRef, useEffect } from 'react'

const APPS = [
  { label: 'Home', href: 'https://dash.mees.st', icon: '\u2302' },
  { label: 'Journal', href: 'https://journal.mees.st', icon: '\u25EB' },
  { label: 'Finance', href: 'https://finance.mees.st', icon: '\u00A3' },
  { label: 'Wine', href: 'https://wine.mees.st', icon: '\uD83C\uDF77' },
  { label: 'Pipeline', href: 'https://pipeline.mees.st', icon: '\u25B6' },
  { label: 'Music', href: 'https://music.mees.st', icon: '\u266B' },
  { label: 'Locations', href: 'https://locations.mees.st', icon: '\u25CE' },
]

const EXTERNAL = [
  { label: 'Healthchecks', href: 'https://hc.mees.st' },
  { label: 'Grafana', href: 'https://grafana.mees.st' },
  { label: 'Paperless', href: 'https://docs.mees.st' },
  { label: 'Immich', href: 'https://pix.mees.st' },
  { label: 'Plex', href: 'https://plex.mees.st' },
]

export default function AppSwitcher() {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [open])

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="p-1.5 rounded hover:bg-bg-hover transition-colors text-text-secondary hover:text-text-primary"
        title="Switch app"
      >
        <svg width="18" height="18" viewBox="0 0 18 18" fill="currentColor">
          <rect x="1" y="1" width="4" height="4" rx="1" />
          <rect x="7" y="1" width="4" height="4" rx="1" />
          <rect x="13" y="1" width="4" height="4" rx="1" />
          <rect x="1" y="7" width="4" height="4" rx="1" />
          <rect x="7" y="7" width="4" height="4" rx="1" />
          <rect x="13" y="7" width="4" height="4" rx="1" />
          <rect x="1" y="13" width="4" height="4" rx="1" />
          <rect x="7" y="13" width="4" height="4" rx="1" />
          <rect x="13" y="13" width="4" height="4" rx="1" />
        </svg>
      </button>
      {open && (
        <div className="absolute left-0 top-full mt-1 z-50 bg-bg-card border border-border rounded-lg shadow-lg p-3 w-56">
          <div className="grid grid-cols-3 gap-1 mb-2">
            {APPS.map((app) => (
              <a
                key={app.label}
                href={app.href}
                className="flex flex-col items-center gap-1 p-2 rounded hover:bg-bg-hover transition-colors text-center"
              >
                <span className="text-lg">{app.icon}</span>
                <span className="text-[11px] text-text-secondary leading-tight">{app.label}</span>
              </a>
            ))}
          </div>
          <div className="border-t border-border pt-2">
            {EXTERNAL.map((link) => (
              <a
                key={link.label}
                href={link.href}
                className="block px-2 py-1 text-xs text-text-secondary hover:text-text-primary hover:bg-bg-hover rounded transition-colors"
              >
                {link.label}
              </a>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
