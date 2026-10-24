import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';
import Alert from '@mui/material/Alert';
import Snackbar from '@mui/material/Snackbar';

interface ToastState {
  message: string;
  severity: 'success' | 'error' | 'info';
  key: number;
}

interface ToastContextValue {
  toast: (message: string, severity?: ToastState['severity']) => void;
  toastError: (message: string) => void;
  toastSuccess: (message: string) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<ToastState | null>(null);

  const toast = useCallback((message: string, severity: ToastState['severity'] = 'info') => {
    setState({ message, severity, key: Date.now() });
  }, []);

  const value = useMemo<ToastContextValue>(
    () => ({
      toast,
      toastError: (message: string) => toast(message, 'error'),
      toastSuccess: (message: string) => toast(message, 'success'),
    }),
    [toast],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      <Snackbar
        key={state?.key}
        open={state !== null}
        autoHideDuration={5000}
        onClose={() => setState(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
      >
        <Alert severity={state?.severity ?? 'info'} variant="filled" onClose={() => setState(null)}>
          {state?.message}
        </Alert>
      </Snackbar>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used within ToastProvider');
  return ctx;
}
