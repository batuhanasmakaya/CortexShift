import {
  ArrowRight, CircleDot, Files, FolderOpen, FolderPlus, LayoutGrid, Layers3,
  Plus, RefreshCw, Settings2, TerminalSquare, Trash2,
} from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { ActionBanner, actionPending, Dialog, LaunchDialog, RecoveryDialog, useActionStatus } from './AgentControl';
import { NewTask } from './NewTask';
import { Records, Sessions } from './History';
import type { RecordLink, RecordKind } from './History';
import { desktop, errorMessage, folderName, operationId, uncertainTransport } from './ipc';
import type { AgentAction, Project, Task } from './ipc';
import { WorkspaceOverview } from './Overview';
import { useRead } from './read';
import { DesktopUpdates } from './DesktopUpdates';
import { Tasks } from './Tasks';
import { Details, Empty, ErrorNotice, Fact, ReadView, SectionHeading, StatusIndicator } from './ui';

type View = 'overview' | 'tasks' | 'sessions' | 'records' | 'diagnostics';
type Theme = 'system' | 'light' | 'dark';
type Screen = { view: View; taskId?: string; sessionId?: string; record?: RecordLink; kind?: RecordKind };

type Modal = { kind: 'task'; active: Task | null } | { kind: 'launch'; action: AgentAction } |
  { kind: 'recover' } | { kind: 'activate'; task: Task; active: Task | null };

const navigation = [
  { view: 'overview', label: 'Overview', Icon: LayoutGrid },
  { view: 'tasks', label: 'Tasks', Icon: Files },
  { view: 'sessions', label: 'Sessions', Icon: CircleDot },
  { view: 'records', label: 'Checkpoints & handoffs', Icon: Layers3 },
] as const;

function Mark() {
  return <span className="mark" aria-hidden="true"><i /><i /></span>;
}

export default function App() {
  const [projects, setProjects] = useState<Project[]>();
  const [selected, setSelected] = useState<string>();
  const [screen, setScreen] = useState<Screen>({ view: 'overview' });
  const [notice, setNotice] = useState<string>();
  const [pending, setPending] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [dataRevision, setDataRevision] = useState(0);
  const [modal, setModal] = useState<Modal>();
  const [result, setResult] = useState<string>();
  const [mutationPending, setMutationPending] = useState(false);
  const refreshData = useCallback(() => setDataRevision(value => value + 1), []);
  const [theme, setTheme] = useState<Theme>(() => {
    const stored = window.localStorage.getItem('cortexshift-theme');
    return stored === 'light' || stored === 'dark' ? stored : 'system';
  });

  useEffect(() => {
    let current = true;
    void desktop.library().then(
      entries => { if (current) setProjects(entries); },
      error => { if (current) { setNotice(errorMessage(error)); setProjects([]); } },
    );
    return () => { current = false; };
  }, [refresh]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem('cortexshift-theme', theme);
  }, [theme]);

  const project = projects?.find(entry => entry.root === selected) ?? projects?.[0];
  const root = project?.root;
  const overview = useRead(useCallback(() => (
    root ? desktop.query('overview', { root }) : Promise.resolve(null)
  ), [root]), refresh + dataRevision);
  const activity = useActionStatus(root, refreshData);
  const current = overview.state === 'ready' ? overview.value : null;
  const blocked = mutationPending || actionPending(activity.receipt) ? 'A Desktop invocation is pending or running.'
    : current?.workspace_busy === true ? 'A managed invocation owns this workspace. Return after it finishes.'
      : current?.workspace_busy !== false ? 'Workspace ownership is unknown. Refresh and check activity.'
        : current.unfinalized_sessions ? 'Review recovery for the unfinalized invocation before continuing.' : null;
  const name = (overview.state === 'ready' ? overview.value?.name : undefined) ?? (root && folderName(root));

  async function manageProject(remove?: string) {
    setPending(true);
    setNotice(undefined);
    setResult(undefined);
    try {
      if (remove) {
        setProjects(await desktop.removeProject(remove));
        setSelected(undefined);
        setScreen({ view: 'overview' });
        return;
      }
      const entry = await desktop.openProject();
      if (entry) {
        setProjects(await desktop.library());
        setSelected(entry.root);
        setScreen({ view: 'overview' });
        setRefresh(value => value + 1);
      }
    } catch (error) {
      setNotice(errorMessage(error));
    } finally {
      setPending(false);
    }
  }

  function selectProject(path: string) {
    setSelected(path);
    setResult(undefined);
    setModal(undefined);
    setScreen({ view: 'overview' });
    setNotice(undefined);
  }

  const openTask = (id: string | null) => setScreen({ view: 'tasks', taskId: id ?? undefined });
  const openRecord = (record: RecordLink | null) =>
    setScreen({ view: 'records', taskId: screen.view === 'overview' ? undefined : screen.taskId,
      record: record ?? undefined, kind: record?.kind ?? screen.kind });

  const launch = (action: AgentAction) => setModal({ kind: 'launch', action });
  const recover = () => setModal({ kind: 'recover' });
  const close = () => setModal(undefined);
  function actionChanged(message?: string) {
    setResult(message);
    close();
    activity.check();
    refreshData();
  }

  function savedTask(id: string, action?: AgentAction, message?: string) {
    openTask(id);
    actionChanged(message);
    if (!root || !action) return;
    setMutationPending(true);
    void (async () => {
      try {
        const plan = await desktop.control('plan', root, { action });
        await desktop.control('submit', root, { action, plan, operation_id: operationId() });
        setResult('Task saved. Check the native terminal and Desktop action status.');
      } catch (error) {
        setResult(uncertainTransport(error)
          ? 'Task saved. The launch outcome is uncertain. Check action status and Session history before retrying.'
          : `Task saved. Launch could not start. ${errorMessage(error)}`);
      } finally {
        activity.check();
        refreshData();
        setMutationPending(false);
      }
    })();
  }

  async function activateTask(task: Task, active: Task | null) {
    if (!root || mutationPending) return;
    setMutationPending(true);
    try {
      await desktop.control('activate_task', root, { task_id: task.id, expected_active: active?.id ?? null });
      actionChanged('Task is active.');
    } catch (error) {
      actionChanged(uncertainTransport(error)
        ? 'Activation outcome is uncertain. Check the active Task before retrying.' : errorMessage(error));
    } finally { setMutationPending(false); }
  }

  return <div className="app-shell">
    <a className="skip-link" href="#main">Skip to content</a>
    <aside className="sidebar">
      <div className="product"><Mark /><span>CortexShift<small>Desktop</small></span></div>
      <div className="library">
        <div className="side-heading"><span>Projects</span>
          <button className="icon-button" aria-label="Open project" title="Open project"
            disabled={pending || mutationPending || projects === undefined} onClick={() => void manageProject()}><FolderPlus /></button>
        </div>
        {projects?.length ? <ul className="project-list" aria-label="Project library">
          {projects.map(entry => <li key={entry.root} className={entry.root === root ? 'selected' : ''}>
            <button className="project-entry" title={entry.root} aria-label={entry.root === root ? name : folderName(entry.root)} aria-pressed={entry.root === root}
              disabled={mutationPending} onClick={() => selectProject(entry.root)}><FolderOpen />
              <span><strong>{entry.root === root ? name : folderName(entry.root)}</strong>
                <small>{entry.root === root
                  ? overview.state === 'error' ? 'Needs attention'
                    : overview.state === 'loading' ? 'Verifying…'
                      : overview.value?.workspace_busy === true ? 'Managed lock held' : 'Verified workspace'
                  : 'Registered workspace'}</small>
              </span>
            </button>
          </li>)}
        </ul> : <p className="sidebar-empty">{projects === undefined ? 'Loading library…' : 'No projects open'}</p>}
      </div>
      {root && <nav className="navigation" aria-label="Workspace">
        <div className="side-heading">Workspace</div>
        {navigation.map(({ view, label, Icon }) => <button key={view} className="nav-item"
          aria-current={screen.view === view ? 'page' : undefined} onClick={() => setScreen({ view })}><Icon />{label}</button>)}
      </nav>}
      <div className="sidebar-bottom">
        <button className="nav-item" aria-current={screen.view === 'diagnostics' ? 'page' : undefined}
          onClick={() => setScreen({ view: 'diagnostics' })}><Settings2 /> Settings &amp; diagnostics</button>
      </div>
    </aside>

    <div className="workspace">
      <div className="topbar">
        <div className="breadcrumbs"><span>{root ? name : 'Desktop'}</span><ArrowRight />
          <strong>{screen.view === 'diagnostics' ? 'Settings' : root ?
            navigation.find(item => item.view === screen.view)?.label : 'Welcome'}</strong></div>
        <div className="actions">
          {current && <button className="button" disabled={mutationPending} onClick={() =>
            setModal({ kind: 'task', active: current.active_task })}><Plus /> New Task</button>}
          {root && <button className="icon-button danger" title="Remove project from library"
            aria-label="Remove project from library" disabled={pending || mutationPending}
            onClick={() => void manageProject(root)}><Trash2 /></button>}
          <button className="icon-button" aria-label="Refresh workspace" title="Refresh workspace"
            disabled={pending} onClick={() => { setNotice(undefined); activity.check(); setRefresh(value => value + 1); }}><RefreshCw /></button>
        </div>
      </div>
      <main id="main" tabIndex={-1} className="main">
        {notice && <ErrorNotice message={notice} />}
        {result && <p className="notice" role="status">{result}</p>}
        {root && activity.receipt && <ActionBanner key={activity.receipt.id} root={root} receipt={activity.receipt}
          observeFailed={activity.observationFailed} change={actionChanged}
          openSession={id => setScreen({ view: 'sessions', sessionId: id })} />}
        {screen.view === 'diagnostics' ? <Diagnostics key={refresh} theme={theme} setTheme={setTheme}
          project={project} schema={overview.state === 'ready' ? overview.value?.schema_version : undefined} />
          : !root ? projects === undefined ? <p role="status" className="feedback">Loading project library…</p> :
            <Welcome busy={pending} open={() => void manageProject()} />
          : <div key={JSON.stringify([root, refresh])} className="content">
            {screen.view === 'overview' && <ReadView read={overview}>{data => data && <WorkspaceOverview
              data={data} launch={launch} blocked={blocked} recover={recover} openTask={openTask} openSessions={() => setScreen({ view: 'sessions' })}
              openCheckpoint={id => openRecord({ kind: 'checkpoint', id })} />}</ReadView>}
            {screen.view === 'tasks' && <Tasks root={root} taskId={screen.taskId ?? null} openTask={openTask}
              active={current?.active_task ?? null} verified={current !== null} blocked={blocked} launch={launch}
              activate={task => setModal({ kind: 'activate', task, active: current?.active_task ?? null })}
              revision={dataRevision} openSessions={taskId => setScreen({ view: 'sessions', taskId })}
              openRecords={taskId => setScreen({ view: 'records', taskId })} />}
            {screen.view === 'sessions' && <Sessions key={JSON.stringify([screen.taskId, screen.sessionId])} root={root} taskId={screen.taskId ?? null}
              focusSession={screen.sessionId} activeId={current?.active_task?.id ?? null}
              blocked={blocked} launch={launch} recover={recover} revision={dataRevision}
              clearTask={() => setScreen({ view: 'sessions' })} openTask={openTask} openRecord={openRecord} />}
            {screen.view === 'records' && <Records key={JSON.stringify([screen.taskId, screen.kind])} root={root}
              kind={screen.kind ?? 'checkpoint'} selected={screen.record?.id ?? null} taskId={screen.taskId ?? null}
              changeKind={kind => setScreen({ view: 'records', taskId: screen.taskId, kind })}
              clearTask={() => setScreen({ view: 'records', kind: screen.kind })} openRecord={openRecord} />}
          </div>}
        {root && modal?.kind === 'task' && <NewTask root={root} active={modal.active}
          blocked={blocked} close={close} saved={savedTask} observe={refreshData} />}
        {root && modal?.kind === 'launch' && <LaunchDialog root={root} initialAction={modal.action}
          close={close} submitted={actionChanged} />}
        {root && modal?.kind === 'recover' && <RecoveryDialog root={root} close={close} complete={actionChanged} />}
        {root && modal?.kind === 'activate' && <Dialog title="Make Task active" busy={mutationPending} close={close}>
          <p>Activate {modal.task.title}?</p>
          {modal.active && <p className="muted">{modal.active.title} will remain available in Task history.</p>}
          <footer className="dialog-actions">
            <button className="button" onClick={close} disabled={mutationPending}>Cancel</button>
            <button className="button primary" disabled={mutationPending}
              onClick={() => void activateTask(modal.task, modal.active)}>Make active</button>
          </footer>
        </Dialog>}
      </main>
    </div>
  </div>;
}

function Welcome({ busy, open }: { busy: boolean; open: () => void }) {
  return <div className="welcome">
    <div className="welcome-inner">
      <Mark />
      <h1>Switch agents.<br />Keep the context.</h1>
      <p>Open a local workspace to view Tasks, agent Sessions, and checkpoints. Agent actions use installed provider CLIs in native terminals.</p>
      <button className="button primary" disabled={busy} onClick={open}><FolderOpen />{busy ? 'Opening project…' : 'Open Project'}<ArrowRight /></button>
      <div className="welcome-note"><TerminalSquare /><span>Choose a folder initialized with <code>cortexshift init</code>.</span></div>
    </div>
  </div>;
}

function Diagnostics({ theme, setTheme, project, schema }: {
  theme: Theme;
  setTheme: (theme: Theme) => void;
  project: Project | undefined;
  schema: number | undefined;
}) {
  const bridge = useRead(useCallback(() => desktop.query('hello', {}), []));
  return <div className="content">
    <SectionHeading title="Settings & diagnostics" subtitle="Appearance and local workspace connectivity." />
    <section className="panel settings">
      <div className="panel-head"><h2>Appearance</h2></div>
      <label htmlFor="theme">Theme</label>
      <select id="theme" value={theme} onChange={event => {
        const value = event.target.value;
        if (value === 'system' || value === 'light' || value === 'dark') setTheme(value);
      }}>
        <option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option>
      </select>
    </section>
    <DesktopUpdates />
    <section className="panel settings">
      <div className="panel-head"><h2>Python bridge</h2></div>
      <ReadView read={bridge}>{value => <Details>
        <Fact title="Connection"><StatusIndicator tone="success">Ready · Local stdio</StatusIndicator></Fact>
        <Fact title="Protocol">{value.protocol}</Fact>
        <Fact title="Core version">{value.core_version}</Fact>
      </Details>}</ReadView>
    </section>
    <section className="panel settings">
      <div className="panel-head"><h2>Selected project</h2></div>
      {project ? <Details>
        <Fact title="Path">{project.root}</Fact>
        <Fact title="Registered project ID"><span className="id">{project.project_id}</span></Fact>
        <Fact title="Verified schema">{schema ?? 'Unknown'}</Fact>
      </Details> : <Empty title="No project selected" />}
    </section>
  </div>;
}
