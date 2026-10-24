import type { ReactNode } from 'react';
import Skeleton from '@mui/material/Skeleton';
import Box from '@mui/material/Box';
import EmptyState from './EmptyState';
import type { IllustrationName } from './Illustration';
import ErrorState from './ErrorState';

export function TableSkeleton({ rows = 5, columns = 5 }: { rows?: number; columns?: number }) {
  return (
    <Box className="fc-skeleton-table" aria-busy="true">
      {Array.from({ length: rows }).map((_, rowIndex) => (
        <Box key={rowIndex} className="fc-skeleton-row">
          {Array.from({ length: columns }).map((__, colIndex) => (
            <Skeleton key={colIndex} height={22} sx={{ flex: 1 }} />
          ))}
        </Box>
      ))}
    </Box>
  );
}

export function CardsSkeleton({ count = 3 }: { count?: number }) {
  return (
    <Box className="metrics-grid" aria-busy="true">
      {Array.from({ length: count }).map((_, i) => (
        <Skeleton key={i} variant="rounded" height={143} />
      ))}
    </Box>
  );
}

/**
 * DataPage wrapper: renders loading skeletons, error or empty states around the page content.
 */
export function DataPage({
  isLoading,
  isError,
  error,
  isEmpty,
  onRetry,
  emptyTitle,
  emptyHint,
  emptyAction,
  emptyIllustration,
  loading = <TableSkeleton />,
  children,
}: {
  isLoading: boolean;
  isError?: boolean;
  error?: unknown;
  isEmpty?: boolean;
  onRetry?: () => void;
  emptyTitle?: string;
  emptyHint?: string;
  emptyAction?: ReactNode;
  /** Defaults to the illustration for the current section of the app. */
  emptyIllustration?: IllustrationName;
  loading?: ReactNode;
  children: ReactNode;
}) {
  if (isLoading) return <>{loading}</>;
  if (isError) return <ErrorState error={error} onRetry={onRetry} />;
  if (isEmpty) {
    return (
      <EmptyState
        illustration={emptyIllustration}
        title={emptyTitle ?? 'Nothing here'}
        hint={emptyHint}
        action={emptyAction}
      />
    );
  }
  return <>{children}</>;
}

export default DataPage;
