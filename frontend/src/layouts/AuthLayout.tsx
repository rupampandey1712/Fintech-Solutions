import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

const FEATURES = [
  { icon: '⇄', text: 'Double-entry ledger with idempotent, atomic transfers' },
  { icon: '⚑', text: 'Real-time fraud scoring, AML alerts and case review' },
  { icon: '✉', text: 'Event-driven notifications over Azure Service Bus' },
];

/**
 * What FinCore does, drawn: a payment passes the fraud check and is posted to the ledger. The pulse
 * travels the path once when the page opens; the rest of the scene is still.
 */
function PaymentFlowScene() {
  return (
    <svg className="auth-scene" viewBox="0 0 520 132" role="img" aria-label="A payment passes a fraud check and is posted to a double-entry ledger">
      <path id="auth-flow" className="auth-scene-path" d="M96 66 C150 66 170 66 226 66 S320 66 372 66" />
      <circle className="auth-scene-pulse" r="5">
        <animateMotion dur="2.4s" begin="0.6s" fill="freeze" calcMode="spline" keySplines="0.22 1 0.36 1" keyTimes="0;1">
          <mpath href="#auth-flow" />
        </animateMotion>
      </circle>

      <g className="auth-scene-card">
        <rect x="12" y="34" width="84" height="56" rx="10" />
        <rect className="chip" x="24" y="48" width="18" height="13" rx="3" />
        <path d="M24 74h44M24 81h26" />
      </g>

      <g className="auth-scene-shield">
        <path d="M260 30l30 11v20c0 19-13 30-30 36-17-6-30-17-30-36V41z" />
        <path className="tick" d="M247 63l9 9 18-19" />
      </g>

      <g className="auth-scene-ledger">
        <rect x="380" y="24" width="128" height="86" rx="10" />
        <path d="M444 34v66" />
        <path d="M392 46h40M392 60h32M392 74h40M456 46h40M456 60h32M456 74h40" />
        <path className="total" d="M392 92h40M456 92h40" />
      </g>
    </svg>
  );
}

export function AuthLayout({ title, subtitle, children }: { title: string; subtitle: string; children: ReactNode }) {
  return (
    <div className="auth-page">
      <div className="auth-aurora" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
      <div className="auth-grid" aria-hidden="true" />

      <section className="auth-hero">
        <div className="auth-brand">
          <span className="brand-mark">F</span>
          <span>fincore</span>
        </div>
        <div className="auth-headline">
          <h2>
            Move money with <em>confidence</em>.
          </h2>
          <p>
            One workspace for payments, risk and operations — running on interchangeable Python and .NET
            backends behind a single contract.
          </p>
          <PaymentFlowScene />
          <ul className="auth-features">
            {FEATURES.map((feature) => (
              <li key={feature.text}>
                <b aria-hidden="true">{feature.icon}</b>
                {feature.text}
              </li>
            ))}
          </ul>
        </div>
        <div className="auth-hero-foot">FINCORE · ENTERPRISE PAYMENTS PLATFORM</div>
      </section>

      <section className="auth-side">
        <div className="auth-card">
          <p className="eyebrow">WELCOME</p>
          <h1>{title}</h1>
          <p className="subtitle">{subtitle}</p>
          {children}
        </div>
        <p className="auth-foot">
          <Link to="/login">Sign in</Link> · <Link to="/register">Create account</Link>
        </p>
      </section>
    </div>
  );
}

export default AuthLayout;
