import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import CssBaseline from '@mui/material/CssBaseline';
import App from './App';
import { AuthProvider } from './hooks/useAuth';
import { ToastProvider } from './hooks/useToast';
import { ColorModeProvider } from './hooks/useColorMode';
import './styles.css';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 15_000 },
  },
});

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <ColorModeProvider>
          <AuthProvider>
            <ToastProvider>
              <CssBaseline />
              <App />
            </ToastProvider>
          </AuthProvider>
        </ColorModeProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
