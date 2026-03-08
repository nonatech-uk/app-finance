import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts'
import { useOverview, useMonthly } from '../hooks/useStats'
import { useSpending } from '../hooks/useCategories'
import { useFavouriteAccounts } from '../hooks/useAccounts'
import { useScope } from '../contexts/ScopeContext'
import StatCard from '../components/common/StatCard'
import CurrencyAmount from '../components/common/CurrencyAmount'
import Badge from '../components/common/Badge'
import LoadingSpinner from '../components/common/LoadingSpinner'

function threeMonthsAgo() {
  const d = new Date()
  d.setMonth(d.getMonth() - 3)
  return d.toISOString().slice(0, 10)
}

function today() {
  return new Date().toISOString().slice(0, 10)
}

export default function Dashboard() {
  const { scope } = useScope()
  const { data: overview, isLoading: overviewLoading } = useOverview(scope)
  const { data: monthly, isLoading: monthlyLoading } = useMonthly({ months: 12, currency: 'GBP', scope })
  const { data: spending } = useSpending({ date_from: threeMonthsAgo(), date_to: today(), currency: 'GBP', scope })
  const { data: favourites } = useFavouriteAccounts(scope)

  const chartData = useMemo(() => {
    if (!monthly) return []
    return [...monthly.items].reverse().map(m => ({
      month: m.month,
      income: parseFloat(m.income),
      expense: Math.abs(parseFloat(m.expense)),
      net: parseFloat(m.net),
    }))
  }, [monthly])

  const topSpending = useMemo(() => {
    if (!spending) return []
    return spending.items
      .filter(s => parseFloat(s.total) < 0 && s.category_path !== 'Uncategorised' && s.category_path !== 'Ignore')
      .slice(0, 12)
      .map(s => ({
        name: s.category_name,
        amount: Math.abs(parseFloat(s.total)),
      }))
  }, [spending])

  if (overviewLoading) return <LoadingSpinner />

  return (
    <div className="space-y-6">
      <h2 className="text-xl font-semibold">Dashboard</h2>

      {overview && (
        <div className="grid grid-cols-4 gap-4">
          <StatCard label="Active Transactions" value={overview.active_transactions.toLocaleString()} subtitle={`${overview.total_raw_transactions.toLocaleString()} raw`} />
          <StatCard label="Accounts" value={overview.active_accounts} subtitle={`${overview.total_accounts} total`} />
          <StatCard label="Dedup Savings" value={overview.removed_by_dedup.toLocaleString()} subtitle={`${overview.dedup_groups} groups`} />
          <StatCard label="Category Coverage" value={`${overview.category_coverage_pct}%`} subtitle={`${overview.date_range_from} — ${overview.date_range_to}`} />
        </div>
      )}

      {/* Favourite Accounts */}
      {favourites && favourites.items.length > 0 && (
        <div>
          <h3 className="text-sm font-medium text-text-secondary mb-3">Favourite Accounts</h3>
          <div className="grid grid-cols-4 gap-4">
            {favourites.items.map(acct => (
              <Link
                key={`${acct.institution}-${acct.account_ref}`}
                to={`/accounts/${acct.institution}/${acct.account_ref}`}
                className="bg-bg-card border border-border rounded-lg p-4 hover:border-accent/50 transition-colors block"
              >
                <div className="flex items-center justify-between mb-1">
                  <div className="text-sm font-medium truncate">
                    {acct.display_name || acct.name || acct.account_ref}
                  </div>
                  <Badge>{acct.currency}</Badge>
                </div>
                {acct.balance && (
                  <div className="text-lg font-semibold">
                    <CurrencyAmount amount={acct.balance} currency={acct.currency} showSign={false} />
                  </div>
                )}
                <div className="text-text-secondary text-xs mt-1">
                  {acct.transaction_count.toLocaleString()} transactions
                </div>
              </Link>
            ))}
          </div>
        </div>
      )}

      <div className="grid grid-cols-2 gap-6">
        {/* Monthly Chart */}
        <div className="bg-bg-card border border-border rounded-lg p-5">
          <h3 className="text-sm font-medium text-text-secondary mb-4">Monthly Income / Expense (GBP)</h3>
          {monthlyLoading ? <LoadingSpinner /> : (
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e4e8" />
                <XAxis dataKey="month" tick={{ fill: '#6b7280', fontSize: 11 }} tickLine={false} />
                <YAxis tick={{ fill: '#6b7280', fontSize: 11 }} tickLine={false} tickFormatter={v => `£${(v / 1000).toFixed(0)}k`} />
                <Tooltip
                  contentStyle={{ background: '#ffffff', border: '1px solid #e2e4e8', borderRadius: 8, color: '#1a1a2e' }}
                  formatter={(value: number | undefined) => [`£${(value ?? 0).toLocaleString('en-GB', { minimumFractionDigits: 0 })}`, '']}
                />
                <Bar dataKey="income" fill="#16a34a" radius={[2, 2, 0, 0]} />
                <Bar dataKey="expense" fill="#dc2626" radius={[2, 2, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>

        {/* Top Spending */}
        <div className="bg-bg-card border border-border rounded-lg p-5">
          <h3 className="text-sm font-medium text-text-secondary mb-4">Top Spending (Last 3 Months, GBP)</h3>
          {topSpending.length === 0 ? <LoadingSpinner /> : (
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={topSpending} layout="vertical" margin={{ left: 80 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e4e8" horizontal={false} />
                <XAxis type="number" tick={{ fill: '#6b7280', fontSize: 11 }} tickFormatter={v => `£${(v / 1000).toFixed(0)}k`} />
                <YAxis type="category" dataKey="name" tick={{ fill: '#6b7280', fontSize: 11 }} width={80} />
                <Tooltip
                  contentStyle={{ background: '#ffffff', border: '1px solid #e2e4e8', borderRadius: 8, color: '#1a1a2e' }}
                  formatter={(value: number | undefined) => [`£${(value ?? 0).toLocaleString('en-GB', { minimumFractionDigits: 2 })}`, 'Spent']}
                />
                <Bar dataKey="amount" fill="#dc2626" radius={[0, 2, 2, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>
    </div>
  )
}
