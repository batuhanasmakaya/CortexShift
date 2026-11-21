import { ArrowLeft, ArrowRight, GitCommitHorizontal, Layers3, Radio } from 'lucide-react';
import { useCallback, useState } from 'react';
import type { Session } from './ipc';
import { date, desktop, label, provider } from './ipc';
import { continuity } from './Overview';
import { useRead } from './read';
import { Details, Empty, Fact, Pager, ReadView, SectionHeading, Status, StatusIndicator, TextList } from './ui';

export type RecordKind = 'checkpoint' | 'handoff';
export type RecordLink = { kind: RecordKind; id: string };
const PAGE_SIZE = 20;

export function Sessions({ root, taskId, clearTask, openTask, openRecord }: {
  root: string;
  taskId: string | null;
  clearTask: () => void;
  openTask: (id: string) => void;
  openRecord: (record: RecordLink) => void;
}) {
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string>();
  const sessions = useRead(useCallback(
    () => desktop.query('sessions', { root, task_id: taskId, offset }), [root, taskId, offset],
  ));
  return <>
    <SectionHeading title="Sessions" subtitle={taskId ? `Task ${taskId}` : 'Managed agent invocations and recorded continuity.'}>
      {taskId && <button className="button" onClick={clearTask}>All Sessions</button>}
    </SectionHeading>
    <ReadView read={sessions}>{data => {
      const current = data.items.find(session => session.id === selected);
      return <>
        {data.items.length ? <div className={current ? 'history-split' : ''}>
          <div className="list" aria-label="Session history">
            {data.items.map(session => <button className="item-row" key={session.id}
              aria-pressed={session.id === selected} onClick={() => setSelected(session.id)}>
              <Radio className="row-icon" />
              <span className="row-main"><strong>{provider(session.provider_id)}</strong>
                <small>{date(session.started_at)}</small><small className="id">{session.id}</small></span>
              <span className="row-meta"><Status value={session.status} uncertain={session.ownership_uncertain} />
                {session.ownership_uncertain && <small>Ownership uncertain</small>}</span><ArrowRight className="row-arrow" />
            </button>)}
          </div>
          {current && <SessionDetail session={current} openTask={openTask} openRecord={openRecord} />}
        </div> : <Empty title="No Sessions on this page">Managed invocations appear here after CortexShift starts an agent.</Empty>}
        <Pager previous={offset > 0 ? offset - PAGE_SIZE : null} next={data.next} onPage={setOffset} />
      </>;
    }}</ReadView>
  </>;
}

function SessionDetail({ session, openTask, openRecord }: {
  session: Session;
  openTask: (id: string) => void;
  openRecord: (record: RecordLink) => void;
}) {
  const { checkpoint, delivery } = session;
  return <section className="panel session-detail" aria-label="Session details">
    <div className="panel-head"><h2>{provider(session.provider_id)}</h2><Status value={session.status} uncertain={session.ownership_uncertain} /></div>
    <p className="id">{session.id}</p>
    <p className="muted">Last recorded Session state{session.ownership_uncertain && ' · Live process ownership is uncertain'}</p>
    <Details>
      <Fact title="Task"><button className="text-button id" onClick={() => openTask(session.task_id)}>{session.task_id} <ArrowRight /></button></Fact>
      <Fact title="Started">{date(session.started_at)}</Fact>
      <Fact title="Ended">{date(session.ended_at)}</Fact>
      {session.reconciled_at && <Fact title="Reconciled">{date(session.reconciled_at)}</Fact>}
      <Fact title="Exit reason">{session.exit_reason ? label(session.exit_reason) : 'Unknown'}</Fact>
      <Fact title="Native continuity">{continuity(session)}</Fact>
      {session.resumed_from_session_id && <Fact title="Resumed from"><span className="id">{session.resumed_from_session_id}</span></Fact>}
      <Fact title="Checkpoint">{checkpoint ?
        <button className="text-button" onClick={() => openRecord({ kind: 'checkpoint', id: checkpoint.id })}>{label(checkpoint.kind)} <ArrowRight /></button> :
        'None recorded'}</Fact>
      <Fact title="Handoff delivery">{delivery ? <>
        <button className="text-button" onClick={() => openRecord({ kind: 'handoff', id: delivery.id })}>{label(delivery.status)} <ArrowRight /></button>
        {delivery.delivered_at && <small>{date(delivery.delivered_at)}</small>}
        {delivery.failure_code && <small>{label(delivery.failure_code)}</small>}
      </> : 'Unknown'}</Fact>
    </Details>
  </section>;
}

export function Records({ root, kind, taskId, selected, changeKind, clearTask, openRecord }: {
  root: string;
  kind: RecordKind;
  taskId: string | null;
  selected: string | null;
  changeKind: (kind: RecordKind) => void;
  clearTask: () => void;
  openRecord: (record: RecordLink | null) => void;
}) {
  const [offset, setOffset] = useState(0);
  const page = useRead(useCallback(() => desktop.query(
    kind === 'checkpoint' ? 'checkpoints' : 'handoffs', { root, task_id: taskId, offset },
  ), [kind, offset, root, taskId]));
  if (selected) return <RecordDetail root={root} record={{ kind, id: selected }} back={() => openRecord(null)} />;
  return <>
    <SectionHeading title="Checkpoints & handoffs" subtitle={taskId ? `Task ${taskId}` : 'Historical observations and context delivery records.'}>
      {taskId && <button className="button" onClick={clearTask}>All records</button>}
    </SectionHeading>
    <div className="segmented" aria-label="Record type">
      <button aria-pressed={kind === 'checkpoint'} onClick={() => changeKind('checkpoint')}>Checkpoints</button>
      <button aria-pressed={kind === 'handoff'} onClick={() => changeKind('handoff')}>Handoffs</button>
    </div>
    <ReadView read={page}>{data => <>
      {data.items.length ? <div className="list" aria-label="Historical records">
        {data.items.map(record => <button className="item-row" key={record.id} onClick={() => openRecord({ kind, id: record.id })}>
          {'kind' in record ? <GitCommitHorizontal className="row-icon" /> : <Layers3 className="row-icon" />}
          <span className="row-main"><strong>{'kind' in record ? label(record.kind) : `${provider(record.source_provider_id)} → ${provider(record.target_provider_id)}`}</strong>
            <small className="id">{record.id}</small><small>Task {record.task_id}</small></span>
          <span className="row-meta">{'status' in record && <Status value={record.status} />}<small>{date(record.created_at)}</small></span>
          <ArrowRight className="row-arrow" />
        </button>)}
      </div> : <Empty title={`No ${kind}s on this page`} />}
      <Pager previous={offset > 0 ? offset - PAGE_SIZE : null} next={data.next} onPage={setOffset} />
    </>}</ReadView>
  </>;
}

function RecordDetail({ root, record: { kind, id }, back }: {
  root: string;
  record: RecordLink;
  back: () => void;
}) {
  const detail = useRead(useCallback(
    () => desktop.query(kind, { root, record_id: id }), [kind, root, id],
  ));
  return <>
    <button className="back text-button" onClick={back}><ArrowLeft /> All {kind}s</button>
    <ReadView read={detail}>{data => <>
      <SectionHeading title={'kind' in data ? `${label(data.kind)} checkpoint` : 'Handoff record'} subtitle={data.id} />
      <div className="notice"><strong>Historical observation</strong><p>Captured {date(data.created_at)} · Task {data.task_id}</p></div>
      <Details>
        <Fact title="Observed Git HEAD"><span className="id">{data.observed_head ?? 'Unknown'}</span></Fact>
        {'status' in data ? <>
          <Fact title="Providers">{provider(data.source_provider_id)} → {provider(data.target_provider_id)}</Fact>
          <Fact title="Delivery"><Status value={data.status} />{data.delivered_at && <small>{date(data.delivered_at)}</small>}
            {data.failure_code && <small>{label(data.failure_code)}</small>}</Fact>
          <Fact title="Source checkpoint"><span className="id">{data.source_checkpoint_id ?? 'Unknown'}</span></Fact>
        </> : <Fact title="Source Session"><span className="id">{data.session_id ?? 'Unknown'}</span></Fact>}
      </Details>
      <section className="panel prose-text"><div className="panel-head"><h2>Test evidence</h2></div>
        <p><StatusIndicator tone={data.test_status.known && data.test_status.provenance === 'reported' ? 'warning' : undefined}>
          {!data.test_status.known ? 'Unknown' : data.test_status.provenance === 'reported' ?
            'Reported · Unverified' : data.test_status.provenance === 'verified' ? 'Verified at capture' : 'Historical test evidence'}
        </StatusIndicator></p>
        <p>{data.test_status.summary}</p>
      </section>
      <div className="details-grid">
        <TextList title="Decisions" data={data.decisions} empty="Unknown" />
        <TextList title="Remaining work" data={data.remaining} />
        <TextList title="Blockers and issues" data={data.issues} />
        {data.operator_note && <section className="detail-section prose-text"><h3>Operator note</h3><p>{data.operator_note}</p></section>}
        {'next_action' in data && <section className="detail-section prose-text"><h3>Recorded next action</h3><p>{data.next_action}</p></section>}
      </div>
    </>}</ReadView>
  </>;
}
