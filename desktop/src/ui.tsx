import { ArrowLeft, ArrowRight, CircleAlert, LoaderCircle } from 'lucide-react';
import type { ReactNode } from 'react';
import type { TextItems } from './ipc';
import { label } from './ipc';
import type { Read } from './read';

export function ReadView<T>({ read, children }: { read: Read<T>; children: (value: T) => ReactNode }) {
  if (read.state === 'loading') return <p className="feedback" role="status"><LoaderCircle className="spin" /> Loading workspace data…</p>;
  if (read.state === 'error') return <ErrorNotice message={read.message} />;
  return children(read.value);
}

export function ErrorNotice({ message }: { message: string }) {
  return <div className="feedback error" role="alert"><CircleAlert /> <span>{message}</span></div>;
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="empty"><strong>{title}</strong>{children && <p>{children}</p>}</div>;
}

export function Status({ value, uncertain = false }: { value: string; uncertain?: boolean }) {
  return <span className="status" data-tone={uncertain ? 'attention' : value === 'completed' || value === 'delivered' ? 'positive' : 'neutral'}>
    <span className="status-dot" />{label(value)}
  </span>;
}

export function TextList({ title, data, empty = 'None recorded' }: { title: string; data: TextItems; empty?: string }) {
  return <section className="detail-section">
    <h3>{title}</h3>
    {data.items.length ? <ul className="text-list">{data.items.map((value, index) =>
      <li key={index}>{value}</li>)}</ul> : <p className="muted">{empty}</p>}
    {(data.truncated || data.total > data.items.length) &&
      <p className="muted">Showing {data.items.length} of {data.total}{data.truncated && ' · Some entries shortened'}</p>}
  </section>;
}

export function Pager({ previous, next, onPage }: { previous: number | null; next: number | null; onPage: (offset: number) => void }) {
  if (previous === null && next === null) return null;
  return <div className="pager">
    <button className="button" disabled={previous === null} onClick={() => previous !== null && onPage(previous)}><ArrowLeft /> Previous</button>
    <button className="button" disabled={next === null} onClick={() => next !== null && onPage(next)}>Next <ArrowRight /></button>
  </div>;
}

export function SectionHeading({ title, subtitle, children }: { title: string; subtitle?: string; children?: ReactNode }) {
  return <header className="section-heading">
    <div><h1>{title}</h1>{subtitle && <p>{subtitle}</p>}</div>
    {children && <div className="actions">{children}</div>}
  </header>;
}

export function Details({ children }: { children: ReactNode }) {
  return <dl className="facts">{children}</dl>;
}

export function Fact({ title, children }: { title: string; children: ReactNode }) {
  return <div><dt>{title}</dt><dd>{children}</dd></div>;
}
