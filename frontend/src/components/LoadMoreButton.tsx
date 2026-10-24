import Button from '@mui/material/Button';
import CircularProgress from '@mui/material/CircularProgress';

export function LoadMoreButton({
  hasNext,
  isLoading,
  onClick,
}: {
  hasNext: boolean;
  isLoading: boolean;
  onClick: () => void;
}) {
  if (!hasNext) return null;
  return (
    <div className="fc-load-more">
      <Button variant="outlined" onClick={onClick} disabled={isLoading} startIcon={isLoading ? <CircularProgress size={14} /> : undefined}>
        Load more
      </Button>
    </div>
  );
}

export default LoadMoreButton;
