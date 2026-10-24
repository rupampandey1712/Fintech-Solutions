import axios from 'axios';

export type Backend = 'python' | 'dotnet';

const configuredDefault = import.meta.env.VITE_API_URL || 'http://localhost:8000';
export const defaultBackend: Backend = configuredDefault.includes(':5000') ? 'dotnet' : 'python';

export const API_URLS: Record<Backend, string> = {
  python: import.meta.env.VITE_PYTHON_API_URL || (defaultBackend === 'python' ? configuredDefault : 'http://localhost:8000'),
  dotnet: import.meta.env.VITE_DOTNET_API_URL || (defaultBackend === 'dotnet' ? configuredDefault : 'http://localhost:5000'),
};

export const apiClient = (backend: Backend) => axios.create({
  baseURL: API_URLS[backend],
  timeout: 4000,
  headers: { Accept: 'application/json' },
});

export async function fetchHealth(backend: Backend): Promise<{ status: string }> {
  const { data } = await apiClient(backend).get<{ status: string }>('/health');
  return data;
}

export async function fetchReadiness(backend: Backend): Promise<{ status: string }> {
  const { data } = await apiClient(backend).get<{ status: string }>('/ready');
  return data;
}
