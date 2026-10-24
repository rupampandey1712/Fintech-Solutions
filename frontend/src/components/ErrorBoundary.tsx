import { Component, type ErrorInfo, type ReactNode } from 'react';
import ErrorState from './ErrorState';

/**
 * Contains a render error to the part of the page that threw. Without a boundary, React unmounts
 * the whole app on any render error, so one bad record in one table blanks every screen.
 * Error boundaries must still be class components in React 19.
 */
export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[FinCore] view crashed', error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <ErrorState
          error={new Error('This view hit an unexpected error. The rest of the app still works.')}
          onRetry={() => this.setState({ error: null })}
        />
      );
    }
    return this.props.children;
  }
}

export default ErrorBoundary;
