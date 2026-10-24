import type { ReactNode } from 'react';
import { useLocation } from 'react-router-dom';
import Illustration, { illustrationForPath, type IllustrationName } from './Illustration';

export function EmptyState({
  illustration,
  title,
  hint,
  action,
}: {
  /** Defaults to the illustration for the current section of the app. */
  illustration?: IllustrationName;
  title: string;
  hint?: string;
  action?: ReactNode;
}) {
  const { pathname } = useLocation();
  return (
    <div className="fc-empty">
      <Illustration name={illustration ?? illustrationForPath(pathname)} />
      <strong>{title}</strong>
      {hint ? <p>{hint}</p> : null}
      {action ? <div className="fc-empty-action">{action}</div> : null}
    </div>
  );
}

export default EmptyState;
