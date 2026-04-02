import { NavLink } from 'react-router-dom'
import { useScope } from '../../contexts/ScopeContext'
import AppSwitcher from './AppSwitcher'

const NAV_ITEMS = [
  { to: '/dashboard', label: 'Dashboard', icon: '◫' },
  { to: '/transactions', label: 'Transactions', icon: '⇄' },
  { to: '/accounts', label: 'Accounts', icon: '◰' },
  { to: '/stocks', label: 'Stocks', icon: '◈' },
  { to: '/assets', label: 'Other Assets', icon: '◆' },
  { to: '/categories', label: 'Categories', icon: '⊞' },
  { to: '/merchants', label: 'Merchants', icon: '⊡' },
  { to: '/tags', label: 'Tags', icon: '⊷' },
  { to: '/tag-rules', label: 'Tag Rules', icon: '⊟' },
  { to: '/receipts', label: 'Receipts', icon: '⊘' },
]

const SCOPE_OPTIONS = [
  { value: 'personal', label: 'Personal' },
  { value: 'business', label: 'Business' },
  { value: 'all', label: 'All' },
]

export default function Sidebar() {
  const { scope, setScope, allowedScopes, user, isAdmin } = useScope()

  const visibleScopes = SCOPE_OPTIONS.filter(
    s => s.value === 'all' ? allowedScopes.length > 1 : allowedScopes.includes(s.value),
  )
  const showSwitcher = visibleScopes.length > 1

  return (
    <aside className="w-52 bg-bg-secondary border-r border-border flex flex-col shrink-0">
      <div className="p-4 border-b border-border">
        <div className="flex items-center justify-between">
          <h1 className="text-lg font-bold text-accent">Finance</h1>
          <AppSwitcher />
        </div>
        {showSwitcher && (
          <div className="flex gap-1 mt-2">
            {visibleScopes.map(s => (
              <button
                key={s.value}
                onClick={() => setScope(s.value)}
                className={`px-2 py-0.5 text-xs rounded-full transition-colors ${
                  scope === s.value
                    ? 'bg-accent/15 text-accent font-medium'
                    : 'text-text-secondary hover:text-text-primary hover:bg-bg-hover'
                }`}
              >
                {s.label}
              </button>
            ))}
          </div>
        )}
      </div>
      <nav className="flex-1 p-2 space-y-1">
        {NAV_ITEMS.map(({ to, label, icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors ${
                isActive
                  ? 'bg-accent/15 text-accent font-medium'
                  : 'text-text-secondary hover:text-text-primary hover:bg-bg-hover'
              }`
            }
          >
            <span className="text-base">{icon}</span>
            {label}
          </NavLink>
        ))}
        {isAdmin && (
          <>
            <div className="border-t border-border my-2" />
            <NavLink
              to="/settings"
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors ${
                  isActive
                    ? 'bg-accent/15 text-accent font-medium'
                    : 'text-text-secondary hover:text-text-primary hover:bg-bg-hover'
                }`
              }
            >
              <span className="text-base">⚙</span>
              Settings
            </NavLink>
          </>
        )}
      </nav>
      {user && (
        <div className="p-3 border-t border-border">
          <div className="text-sm text-text-primary truncate">{user.display_name}</div>
          <div className="text-xs text-text-secondary truncate">{user.email}</div>
          {isAdmin && (
            <span className="inline-block mt-1 px-1.5 py-0.5 text-[10px] rounded bg-accent/15 text-accent">
              admin
            </span>
          )}
        </div>
      )}
    </aside>
  )
}
