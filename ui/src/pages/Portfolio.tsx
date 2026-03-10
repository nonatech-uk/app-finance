import { useState } from 'react'
import { Link } from 'react-router-dom'
import { usePortfolio, useCreateHolding, useRefreshPrices } from '../hooks/useStocks'
import { useScope } from '../contexts/ScopeContext'
import StatCard from '../components/common/StatCard'
import CurrencyAmount from '../components/common/CurrencyAmount'
import LoadingSpinner from '../components/common/LoadingSpinner'

export default function Portfolio() {
  const { scope } = useScope()
  const { data, isLoading, error } = usePortfolio(scope)
  const createHolding = useCreateHolding()
  const refreshPrices = useRefreshPrices()
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ symbol: '', name: '', country: 'US', currency: 'USD' })

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault()
    createHolding.mutate(form, {
      onSuccess: () => {
        setShowForm(false)
        setForm({ symbol: '', name: '', country: 'US', currency: 'USD' })
      },
    })
  }

  if (isLoading) return <LoadingSpinner />
  if (error) return <div className="text-expense">Error loading portfolio</div>

  const pnl = parseFloat(data?.unrealised_pnl || '0')
  const pnlPct = data?.unrealised_pnl_pct || '0'
  const gbpPnl = parseFloat(data?.gbp_pnl || '0')

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold">Stocks</h2>
        <div className="flex gap-2">
          <button
            onClick={() => refreshPrices.mutate()}
            disabled={refreshPrices.isPending}
            className="px-3 py-1.5 text-sm rounded-md bg-bg-secondary border border-border text-text-primary hover:bg-bg-hover disabled:opacity-50"
          >
            {refreshPrices.isPending ? 'Refreshing...' : 'Refresh Prices'}
          </button>
          <button
            onClick={() => setShowForm(!showForm)}
            className="px-3 py-1.5 text-sm rounded-md bg-accent text-white hover:bg-accent/90"
          >
            + Add Holding
          </button>
        </div>
      </div>

      {showForm && (
        <form onSubmit={handleCreate} className="bg-bg-card border border-border rounded-lg p-4 flex gap-3 items-end">
          <div>
            <label className="block text-xs text-text-secondary mb-1">Symbol</label>
            <input
              value={form.symbol}
              onChange={e => setForm({ ...form, symbol: e.target.value })}
              placeholder="AAPL"
              required
              className="w-24 px-2 py-1.5 text-sm rounded border border-border bg-bg-primary text-text-primary"
            />
          </div>
          <div className="flex-1">
            <label className="block text-xs text-text-secondary mb-1">Name</label>
            <input
              value={form.name}
              onChange={e => setForm({ ...form, name: e.target.value })}
              placeholder="Apple Inc."
              required
              className="w-full px-2 py-1.5 text-sm rounded border border-border bg-bg-primary text-text-primary"
            />
          </div>
          <div>
            <label className="block text-xs text-text-secondary mb-1">Country</label>
            <input
              value={form.country}
              onChange={e => setForm({ ...form, country: e.target.value })}
              className="w-16 px-2 py-1.5 text-sm rounded border border-border bg-bg-primary text-text-primary"
            />
          </div>
          <div>
            <label className="block text-xs text-text-secondary mb-1">Currency</label>
            <input
              value={form.currency}
              onChange={e => setForm({ ...form, currency: e.target.value })}
              className="w-16 px-2 py-1.5 text-sm rounded border border-border bg-bg-primary text-text-primary"
            />
          </div>
          <button
            type="submit"
            disabled={createHolding.isPending}
            className="px-3 py-1.5 text-sm rounded-md bg-accent text-white hover:bg-accent/90 disabled:opacity-50"
          >
            {createHolding.isPending ? 'Creating...' : 'Create'}
          </button>
          <button
            type="button"
            onClick={() => setShowForm(false)}
            className="px-3 py-1.5 text-sm rounded-md border border-border text-text-secondary hover:bg-bg-hover"
          >
            Cancel
          </button>
        </form>
      )}

      {data && (
        <div className="grid grid-cols-3 gap-4">
          <StatCard
            label="GBP Value"
            value={`£${parseFloat(data.total_gbp_value).toLocaleString('en-GB', { minimumFractionDigits: 2 })}`}
            subtitle={data.price_date ? `as of ${data.price_date}` : undefined}
          />
          <StatCard
            label="GBP Cost"
            value={parseFloat(data.total_gbp_cost) > 0 ? `£${parseFloat(data.total_gbp_cost).toLocaleString('en-GB', { minimumFractionDigits: 2 })}` : '-'}
          />
          <StatCard
            label="GBP P&L"
            value={gbpPnl !== 0 ? `${gbpPnl >= 0 ? '+' : ''}£${gbpPnl.toLocaleString('en-GB', { minimumFractionDigits: 2 })}` : '-'}
            subtitle={gbpPnl !== 0 ? `${parseFloat(data.gbp_pnl_pct) >= 0 ? '+' : ''}${data.gbp_pnl_pct}%` : undefined}
            className={gbpPnl !== 0 ? (gbpPnl >= 0 ? 'border-income/30' : 'border-expense/30') : undefined}
          />
        </div>
      )}

      {data && data.holdings.length > 0 && (
        <div className="bg-bg-card border border-border rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-bg-secondary">
                <th className="text-left px-4 py-2.5 font-medium text-text-secondary">Symbol</th>
                <th className="text-left px-4 py-2.5 font-medium text-text-secondary">Name</th>
                <th className="text-right px-4 py-2.5 font-medium text-text-secondary">Shares</th>
                <th className="text-right px-4 py-2.5 font-medium text-text-secondary">Price</th>
                <th className="text-right px-4 py-2.5 font-medium text-text-secondary">GBP Value</th>
                <th className="text-right px-4 py-2.5 font-medium text-text-secondary">GBP Cost</th>
                <th className="text-right px-4 py-2.5 font-medium text-text-secondary">GBP P&L</th>
                <th className="text-right px-4 py-2.5 font-medium text-text-secondary">%</th>
              </tr>
            </thead>
            <tbody>
              {data.holdings.map(h => {
                const gbpPnlVal = parseFloat(h.gbp_pnl || '0')
                const ccySymbol = h.currency === 'GBP' ? '' : h.currency + ' '
                return (
                  <tr key={h.id} className="border-b border-border last:border-0 hover:bg-bg-hover">
                    <td className="px-4 py-2.5">
                      <Link to={`/stocks/${h.id}`} className="font-medium text-accent hover:underline">
                        {h.symbol}
                      </Link>
                    </td>
                    <td className="px-4 py-2.5 text-text-secondary">{h.name}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums">{parseFloat(h.current_shares || '0')}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums">
                      {h.current_price ? (
                        <span title={h.fx_rate && h.currency !== 'GBP' ? `FX: 1 ${h.currency} = £${parseFloat(h.fx_rate).toFixed(4)}` : undefined}>
                          {ccySymbol}{parseFloat(h.current_price).toFixed(2)}
                        </span>
                      ) : '-'}
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums">
                      {h.gbp_current_value ? `£${parseFloat(h.gbp_current_value).toLocaleString('en-GB', { minimumFractionDigits: 2 })}` : '-'}
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums">
                      {h.gbp_total_cost ? `£${parseFloat(h.gbp_total_cost).toLocaleString('en-GB', { minimumFractionDigits: 2 })}` : '-'}
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      {h.gbp_pnl ? (
                        <CurrencyAmount amount={h.gbp_pnl} currency="GBP" />
                      ) : '-'}
                    </td>
                    <td className={`px-4 py-2.5 text-right tabular-nums font-medium ${gbpPnlVal >= 0 ? 'text-income' : 'text-expense'}`}>
                      {h.gbp_pnl_pct ? `${gbpPnlVal >= 0 ? '+' : ''}${h.gbp_pnl_pct}%` : '-'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {data && data.holdings.length === 0 && (
        <div className="bg-bg-card border border-border rounded-lg p-8 text-center text-text-secondary">
          No holdings yet. Add one to get started.
        </div>
      )}

      <div>
        <Link
          to="/stocks/tax"
          className="text-sm text-accent hover:underline"
        >
          View Tax Summary &rarr;
        </Link>
      </div>
    </div>
  )
}
