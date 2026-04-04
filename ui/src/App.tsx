import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Shell from './components/layout/Shell'
import { ScopeProvider } from './contexts/ScopeContext'
import Dashboard from './pages/Dashboard'
import Transactions from './pages/Transactions'
import Accounts from './pages/Accounts'
import AccountDetail from './pages/AccountDetail'
import Categories from './pages/Categories'
import Merchants from './pages/Merchants'
import Portfolio from './pages/Portfolio'
import HoldingDetail from './pages/HoldingDetail'
import TaxSummary from './pages/TaxSummary'
import Assets from './pages/Assets'
import AssetDetail from './pages/AssetDetail'
import TagRules from './pages/TagRules'
import Tags from './pages/Tags'
import Settings from './pages/Settings'
import Receipts from './pages/Receipts'
import Splitwise from './pages/Splitwise'

export default function App() {
  return (
    <BrowserRouter>
      <ScopeProvider>
        <Shell>
          <Routes>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/transactions" element={<Transactions />} />
            <Route path="/accounts" element={<Accounts />} />
            <Route path="/accounts/:institution/:accountRef" element={<AccountDetail />} />
            <Route path="/stocks" element={<Portfolio />} />
            <Route path="/stocks/tax" element={<TaxSummary />} />
            <Route path="/stocks/:holdingId" element={<HoldingDetail />} />
            <Route path="/assets" element={<Assets />} />
            <Route path="/assets/:holdingId" element={<AssetDetail />} />
            <Route path="/categories" element={<Categories />} />
            <Route path="/merchants" element={<Merchants />} />
            <Route path="/tags" element={<Tags />} />
            <Route path="/tag-rules" element={<TagRules />} />
            <Route path="/receipts" element={<Receipts />} />
            <Route path="/splitwise" element={<Splitwise />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<div className="text-text-secondary">Page not found</div>} />
          </Routes>
        </Shell>
      </ScopeProvider>
    </BrowserRouter>
  )
}
