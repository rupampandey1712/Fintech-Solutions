import Button from '@mui/material/Button';
import { problemMessage } from '../utils/format';
import Illustration from './Illustration';

export function ErrorState({
  error,
  onRetry,
  hint,
}: {
  error: unknown;
  onRetry?: () => void;
  hint?: string;
}) {
  const status = (error as { response?: { status?: number } })?.response?.status;
  const forbidden = status === 403;
  const message = forbidden
    ? 'You do not have permission to view this section. Administrator or analyst access is required.'
    : problemMessage(error);

  return (
    <div className="fc-error" role="alert">
      <Illustration name={forbidden ? 'forbidden' : 'error'} />
      <strong>{forbidden ? 'Permission required' : 'Something went wrong'}</strong>
      <p>{hint ? `${message} (${hint})` : message}</p>
      {onRetry && !forbidden ? (
        <Button variant="outlined" size="small" onClick={onRetry}>
          Try again
        </Button>
      ) : null}
    </div>
  );
}

export default ErrorState;
