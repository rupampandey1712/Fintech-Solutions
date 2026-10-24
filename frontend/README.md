# FinCore Frontend

Shared React + TypeScript client. Configure `VITE_API_URL` to select either backend (the
backend toggle in the topbar persists to localStorage; `VITE_PYTHON_API_URL` and
`VITE_DOTNET_API_URL` provide both targets). UI formats inputs for usability; financial and
authorization rules remain backend-owned.

Pages: dashboard, accounts, payments (create/track/dispute), transactions, beneficiaries,
KYC, notifications, fraud, AML, reconciliation, reports (with CSV exports), treasury
(deposits, refunds/reversals, settlement batches) and admin (customers, user roles, fraud
rules, dead letters, audit logs).

## Run

```bash
npm ci
npm run dev      # http://localhost:5173
npm run build    # typecheck + production build
```
