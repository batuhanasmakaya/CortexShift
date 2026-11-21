import { ArrowLeft, ArrowRight, Check, Files } from 'lucide-react';
import { useCallback, useState } from 'react';
import { date, desktop } from './ipc';
import { useRead } from './read';
import { Details, Empty, Fact, Pager, ReadView, SectionHeading, Status, TextList } from './ui';

const PAGE_SIZE = 20;
const OBJECTIVE_SIZE = 8192;

export function Tasks({ root, taskId, openTask, openSessions, openRecords }: {
  root: string;
  taskId: string | null;
  openTask: (id: string | null) => void;
  openSessions: (id: string) => void;
  openRecords: (id: string) => void;
}) {
  const [offset, setOffset] = useState(0);
  const tasks = useRead(useCallback(() => desktop.query('tasks', { root, offset }), [root, offset]));
  if (taskId) return <TaskInspector key={taskId} root={root} taskId={taskId}
    back={() => openTask(null)} openSessions={() => openSessions(taskId)} openRecords={() => openRecords(taskId)} />;
  return <>
    <SectionHeading title="Tasks" subtitle="Objectives and progress recorded in this workspace." />
    <ReadView read={tasks}>{data => data.items.length ? <>
      <div className="list" aria-label="Workspace Tasks">
        {data.items.map(task => <button className="item-row" key={task.id} onClick={() => openTask(task.id)}>
          <Files className="row-icon" />
          <span className="row-main"><strong>{task.title}</strong><small className="preview">{task.objective_preview}</small>
            <small className="id">{task.id}</small></span>
          <span className="row-meta"><Status value={task.status} /><small>{task.session_count} {task.session_count === 1 ? 'Session' : 'Sessions'}</small><small>Updated {date(task.updated_at)}</small></span>
          <ArrowRight className="row-arrow" />
        </button>)}
      </div>
      <Pager previous={offset > 0 ? offset - PAGE_SIZE : null} next={data.next} onPage={setOffset} />
    </> : <>
      <Empty title="No Tasks on this page">Tasks created in the CortexShift CLI appear here.</Empty>
      <Pager previous={offset > 0 ? offset - PAGE_SIZE : null} next={null} onPage={setOffset} />
    </>}</ReadView>
  </>;
}

function TaskInspector({ root, taskId, back, openSessions, openRecords }: {
  root: string;
  taskId: string;
  back: () => void;
  openSessions: () => void;
  openRecords: () => void;
}) {
  const task = useRead(useCallback(() => desktop.query('task', { root, task_id: taskId }), [root, taskId]));
  return <>
    <button className="back text-button" onClick={back}><ArrowLeft /> All Tasks</button>
    <ReadView read={task}>{data => <>
      <SectionHeading title={data.title} subtitle={data.id}>
        <button className="button" onClick={openSessions}>Sessions <ArrowRight /></button>
        <button className="button" onClick={openRecords}>Checkpoints &amp; handoffs <ArrowRight /></button>
      </SectionHeading>
      <Details>
        <Fact title="Lifecycle"><Status value={data.status} /></Fact>
        <Fact title="Created">{date(data.created_at)}</Fact>
        <Fact title="Updated">{date(data.updated_at)}</Fact>
      </Details>
      <Objective root={root} taskId={taskId} />
      <div className="details-grid">
        <TextList title="Requirements" data={data.requirements} />
        <TextList title="Constraints" data={data.constraints} />
        <TextList title="Completed" data={data.completed} />
        <TextList title="Remaining work" data={data.remaining} />
        <TextList title="Decisions from checkpoints" data={data.decisions} empty="Unknown" />
        <TextList title="Outstanding issues" data={data.issues} />
      </div>
      <section className="detail-section prose-text"><h3><Check /> Current work</h3><p>{data.current_work ?? 'None recorded'}</p></section>
    </>}</ReadView>
  </>;
}

function Objective({ root, taskId }: { root: string; taskId: string }) {
  const [offset, setOffset] = useState(0);
  const objective = useRead(useCallback(
    () => desktop.query('objective', { root, task_id: taskId, offset }), [root, taskId, offset],
  ));
  return <section className="panel objective">
    <div className="panel-head"><h2>Objective</h2><span className="muted">Markdown source</span></div>
    <ReadView read={objective}>{data => <>
      <pre>{data.text || 'No objective text recorded.'}</pre>
      <Pager previous={offset > 0 ? offset - OBJECTIVE_SIZE : null} next={data.next} onPage={setOffset} />
    </>}</ReadView>
  </section>;
}
