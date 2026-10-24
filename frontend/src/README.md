# FinCore Frontend

React 19 + TypeScript + Vite 6 SPA for the FinCore payments workspace. Talks to two interchangeable
backends (Python FastAPI on `:8000`, .NET on `:5000`) implementing the same API contract; the active
backend is switchable at runtime from the topbar and persisted in `localStorage` under
`fincore-backend`.

## Stack

- React 19, react-router-dom 7 (`BrowserRouter`), TanStack Query 5 (all GETs, staleTime 15s)
- MUI 6 for forms, tables, dialogs, switches; custom CSS shell (dark sidebar, light content)
- react-hook-form + zod (`@hookform/resolvers`) for every form
- recharts 3 for dashboard/report charts
- axios with a bearer-token interceptor and single-flight refresh retry on 401

## Configuration

Env vars (see `vite.config.ts` / `src/api/client.ts`):

- `VITE_PYTHON_API_URL` (default `http://localhost:8000`)
- `VITE_DOTNET_API_URL` (default `http://localhost:5000`)
- `VITE_API_URL` — overrides the default backend base URL (which backend it maps to is inferred from the port)

## Structure

```
src/
  api/            axios client with auth store, token refresh + backend switcher (client.ts);
                  all typed endpoint functions (endpoints.ts)
  components/     Illustration (inline SVG scenes for empty/error/loading states), ErrorBoundary,
                  StatusChip, CurrencyText, CurrencyPicker (+ useReportCurrency), DataPage
                  (+ skeletons), EmptyState, ErrorState, ConfirmDialog, PageHeader, LoadMoreButton
  features/       feature-scoped components:
    auth/           LoginForm, RegisterForm (zod: email, password ≥12 chars, strength hint)
    accounts/       OpenAccountDialog
    payments/       CreatePaymentForm (Idempotency-Key per submit), PaymentRiskPanel
    beneficiaries/  AddBeneficiaryDialog (UUID validation)
    fraud/          ReviewCaseDialog
    admin/          CustomersTab, FraudRulesTab, AuditLogsTab
  hooks/          useAuth (context + localStorage 'fincore-auth'), useBackend, useToast
  layouts/        AppLayout (sidebar + topbar + Outlet), AuthLayout
  pages/          one component per route (see below)
  routes/         route table + ProtectedRoute (redirects to /login when unauthenticated)
  types/          TS interfaces mirroring the API contract (camelCase)
  utils/          formatMoney (minor units → "1,234.56 USD"), dates, cursor helpers,
                  problem+json message extraction, base64 file reader
  App.tsx, main.tsx, styles.css
```

## Pages

| Route              | Page                                                               |
| ------------------ | ------------------------------------------------------------------ |
| `/login`           | Sign in (public)                                                   |
| `/register`        | Create account (public, password ≥ 12 chars; the API also refuses common passwords) |
| `/`                | Dashboard: metrics, monthly volume bar chart per currency, 5 recent transactions; platform-wide wording for admins |
| `/accounts`        | Account list + "Open account" dialog                               |
| `/accounts/:id`    | Balances, recent account transactions, ledger entries              |
| `/payments`        | Status filter chips, risk chips, cursor "Load more"                |
| `/payments/new`    | Create payment: available balance shown, over-balance amounts flagged (risk/fraud reasons surfaced on 422) |
| `/payments/:id`    | Status timeline + risk panel                                       |
| `/transactions`    | Table with account filter, Load more                               |
| `/transactions/:id`| Full detail, links to account/payment                              |
| `/beneficiaries`   | Add/verify/activate (cooling-aware)/disable lifecycle              |
| `/kyc`             | Application wizard: create → upload docs (base64, 5 MB cap) → submit; history |
| `/notifications`   | Channel cards, refetch every 20s                                   |
| `/fraud`           | Staff: "Fraud cases" with review dialog and the AI Investigate panel (admin/fraud_analyst, when the agent is configured). Customers: "Payment reviews", read-only |
| `/aml`             | Alert table with expandable JSON details                           |
| `/reconciliation`  | Run button + result records; history with expandable run detail    |
| `/reports`         | Dashboard metrics, monthly BarChart, daily LineChart, success-rate Pie; volumes per currency (picker when several) |
| `/admin`           | Tabs: Customers (inline status PATCH), Fraud rules (editable), Audit logs |

Unknown routes redirect to `/`. Role-gated views are always visible but render a
"You do not have permission" state on 403 problem responses.

## Commands

```
npm install
npm run dev       # dev server on :5173
npm run build     # tsc -b && vite build
npm run preview
```
