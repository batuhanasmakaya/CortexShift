import {
  ArrowRight, CircleDot, Files, FolderOpen, FolderPlus, LayoutGrid, Layers3,
  RefreshCw, Settings2, TerminalSquare, Trash2,
} from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { Records, Sessions } from './History';
import type { RecordLink, RecordKind } from './History';
import { desktop, errorMessage, folderName } from './ipc';
import type { Project } from './ipc';
import { WorkspaceOverview } from './Overview';
import { useRead } from './read';
import { Tasks } from './Tasks';
import { Details, Empty, ErrorNotice, Fact, ReadView, SectionHeading, StatusIndicator } from './ui';

type View = 'overview' | 'tasks' | 'sessions' | 'records' | 'diagnostics';
type Theme = 'system' | 'light' | 'dark';
type Screen = { view: View; taskId?: string; record?: RecordLink; kind?: RecordKind };

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
  ), [root]), refresh);
  const name = (overview.state === 'ready' ? overview.value?.name : undefined) ?? (root && folderName(root));

  async function manageProject(remove?: string) {
    setPending(true);
    setNotice(undefined);
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
    setScreen({ view: 'overview' });
    setNotice(undefined);
  }

  const openTask = (id: string | null) => setScreen({ view: 'tasks', taskId: id ?? undefined });
  const openRecord = (record: RecordLink | null) =>
    setScreen({ view: 'records', taskId: screen.view === 'overview' ? undefined : screen.taskId,
      record: record ?? undefined, kind: record?.kind ?? screen.kind });

  return <div className="app-shell">
    <a className="skip-link" href="#main">Skip to content</a>
    <aside className="sidebar">
      <div className="product"><Mark /><span>CortexShift<small>Desktop</small></span></div>
      <div className="library">
        <div className="side-heading"><span>Projects</span>
          <button className="icon-button" aria-label="Open project" title="Open project"
            disabled={pending || projects === undefined} onClick={() => void manageProject()}><FolderPlus /></button>
        </div>
        {projects?.length ? <ul className="project-list" aria-label="Project library">
          {projects.map(entry => <li key={entry.root} className={entry.root === root ? 'selected' : ''}>
            <button className="project-entry" title={entry.root} aria-label={entry.root === root ? name : folderName(entry.root)} aria-pressed={entry.root === root}
              onClick={() => selectProject(entry.root)}><FolderOpen />
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
          {root && <button className="icon-button danger" title="Remove project from library"
            aria-label="Remove project from library" disabled={pending}
            onClick={() => void manageProject(root)}><Trash2 /></button>}
          <button className="icon-button" aria-label="Refresh workspace" title="Refresh workspace"
            disabled={pending} onClick={() => { setNotice(undefined); setRefresh(value => value + 1); }}><RefreshCw /></button>
        </div>
      </div>
      <main id="main" tabIndex={-1} className="main">
        {notice && <ErrorNotice message={notice} />}
        {screen.view === 'diagnostics' ? <Diagnostics key={refresh} theme={theme} setTheme={setTheme}
          project={project} schema={overview.state === 'ready' ? overview.value?.schema_version : undefined} />
          : !root ? projects === undefined ? <p role="status" className="feedback">Loading project library…</p> :
            <Welcome busy={pending} open={() => void manageProject()} />
          : <div key={JSON.stringify([root, refresh])} className="content">
            {screen.view === 'overview' && <ReadView read={overview}>{data => data && <WorkspaceOverview
              data={data} openTask={openTask} openSessions={() => setScreen({ view: 'sessions' })}
              openCheckpoint={id => openRecord({ kind: 'checkpoint', id })} />}</ReadView>}
            {screen.view === 'tasks' && <Tasks root={root} taskId={screen.taskId ?? null} openTask={openTask}
              openSessions={taskId => setScreen({ view: 'sessions', taskId })}
              openRecords={taskId => setScreen({ view: 'records', taskId })} />}
            {screen.view === 'sessions' && <Sessions key={screen.taskId ?? ''} root={root} taskId={screen.taskId ?? null}
              clearTask={() => setScreen({ view: 'sessions' })} openTask={openTask} openRecord={openRecord} />}
            {screen.view === 'records' && <Records key={JSON.stringify([screen.taskId, screen.kind])} root={root}
              kind={screen.kind ?? 'checkpoint'} selected={screen.record?.id ?? null} taskId={screen.taskId ?? null}
              changeKind={kind => setScreen({ view: 'records', taskId: screen.taskId, kind })}
              clearTask={() => setScreen({ view: 'records', kind: screen.kind })} openRecord={openRecord} />}
          </div>}
      </main>
    </div>
  </div>;
}

function Welcome({ busy, open }: { busy: boolean; open: () => void }) {
  return <div className="welcome">
    <div className="welcome-inner">
      <Mark />
      <h1>Switch agents.<br />Keep the context.</h1>
      <p>Open a CortexShift workspace to see the Tasks, agent Sessions, and checkpoints that carry your work across providers.</p>
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
