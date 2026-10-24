import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchNotifications } from '../api/endpoints';
import { getBackend } from '../api/client';
import PageHeader from '../components/PageHeader';
import DataPage, { TableSkeleton } from '../components/DataPage';
import LoadMoreButton from '../components/LoadMoreButton';
import StatusChip from '../components/StatusChip';
import { formatDate, timeAgo } from '../utils/format';
import type { Notification } from '../types';

const bk = () => getBackend();
const PAGE_SIZE = 50;

export function NotificationsPage() {
  const [cursor, setCursor] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ['notifications', bk(), cursor],
    queryFn: () => fetchNotifications({ limit: PAGE_SIZE, cursor }),
    refetchInterval: 20_000,
  });

  const items = query.data?.items ?? [];
  // "Unread-style" emphasis on the latest 10.
  const emphasizedCount = cursor ? 0 : Math.min(10, items.length);

  return (
    <>
      <PageHeader
        eyebrow="INBOX"
        title="Notifications"
        subtitle="Platform events delivered over email, SMS, push and in-app channels."
      />
      <DataPage
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        onRetry={() => void query.refetch()}
        isEmpty={items.length === 0}
        emptyTitle="No notifications"
        emptyHint="Platform events (payments, KYC, fraud) will appear here."
        loading={<TableSkeleton rows={5} columns={4} />}
      >
        <div className="notification-list">
          {items.map((n: Notification, i) => (
            <article key={n.id} className={`notification-card${i < emphasizedCount ? ' unread' : ''}`}>
              <div className="notification-top">
                <span className={`notification-channel ${n.channel}`}>{n.channel.replace('_', '-')}</span>
                <strong className="notification-subject">{n.subject}</strong>
                <span className="notification-when">{formatDate(n.createdAt)} · {timeAgo(n.createdAt)}</span>
              </div>
              <p className="notification-body">{n.body}</p>
              <div className="notification-meta">
                <span className="fc-cell-muted">{n.eventType}</span>
                <StatusChip status={n.status} />
                {n.sentAt ? <span className="fc-cell-muted">sent {timeAgo(n.sentAt)}</span> : null}
              </div>
            </article>
          ))}
        </div>
        <LoadMoreButton
          hasNext={Boolean(query.data?.nextCursor)}
          isLoading={query.isFetching}
          onClick={() => query.data?.nextCursor && setCursor(query.data.nextCursor)}
        />
      </DataPage>
    </>
  );
}

export default NotificationsPage;
