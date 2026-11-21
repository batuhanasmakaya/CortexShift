import { ArrowUpRight, FolderOpen, Workflow } from 'lucide-react';
import type { Overview, Session } from './ipc';
import { date, label, provider } from './ipc';
import { Details, Empty, Fact, SectionHeading, Status, TextList } from './ui';

export function continuity(session: Session) {
  if (session.native_resume) return 'Exact native resume recorded';
  return session.task_continuation ? 'Task-level continuation only' : 'Unknown';
}

export function WorkspaceOverview({ data, openTask, openSessions, openCheckpoint }: {
  data: Overview;
  openTask: (id: string) => void;
  openSessions: () => void;
  openCheckpoint: (id: string) => void;
}) {
  const { active_task: task, latest_session: session, checkpoint: checkpoint } = data;
  const activity = data.workspace_busy === null ? 'Ownership unknown' :
    data.workspace_busy ? 'Managed workspace lock held' : 'No managed lock observed';
  const attention = data.unfinalized_sessions > 0 && data.workspace_busy !== true;
  return <>
    <SectionHeading title={data.name} subtitle="Workspace overview" />
    <div className="path"><FolderOpen /> <span>{data.root}</span></div>
    {attention && <div className="notice" role="status">
      <strong>Recovery attention</strong>
      <p>{data.unfinalized_sessions} unfinalized {data.unfinalized_sessions === 1 ? 'Session' : 'Sessions'}. {data.workspace_busy === null ? 'Workspace ownership could not be verified.' : 'No managed workspace lock was observed.'} Review Session history before using CLI recovery.</p>
    </div>}
    <div className="overview-grid">
      <section className="panel task-panel">
        <div className="panel-head"><h2>Active Task</h2>{task && <Status value={task.status} />}</div>
        {task ? <>
          <button className="title-link" onClick={() => openTask(task.id)}>{task.title} <ArrowUpRight /></button>
          <p className="id">{task.id}</p>
          <TextList title="Recent progress" data={data.completed} />
          {data.current_work && <div className="detail-section prose-text"><h3>Current work</h3><p>{data.current_work}</p></div>}
          <TextList title="Open issues" data={data.issues} />
        </> : <Empty title="No active Task">Tasks appear here when activated in CortexShift.</Empty>}
      </section>
      <div className="overview-side">
        <section className="panel">
          <div className="panel-head"><h2>Workspace activity</h2><Workflow /></div>
          <p className="activity"><span className="status-dot" data-active={data.workspace_busy === true} />{activity}</p>
          <Details>
            <Fact title="Last provider">{session ? provider(session.provider_id) : 'Unknown'}</Fact>
            <Fact title="Latest Session">{session ? <>
              <button className="text-button" onClick={openSessions}>{label(session.status)} <ArrowUpRight /></button>
              <small>{date(session.started_at)} · Last recorded</small>
            </> : 'None recorded'}</Fact>
          </Details>
        </section>
        <section className="panel">
          <div className="panel-head"><h2>Latest checkpoint</h2></div>
          {checkpoint ? <>
            <button className="text-button" onClick={() => openCheckpoint(checkpoint.id)}>{label(checkpoint.kind)} <ArrowUpRight /></button>
            <p className="muted">{date(checkpoint.created_at)} · Historical observation</p>
          </> : <p className="muted">None recorded</p>}
        </section>
      </div>
    </div>
  </>;
}
