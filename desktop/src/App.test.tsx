import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import App from './App';

declare const jsdom: { window: Window };

const tauri = vi.hoisted(() => ({ invoke: vi.fn() }));
vi.mock('@tauri-apps/api/core', () => tauri);

const time = '2026-01-09T12:00:00Z';
const alpha = { root: '/work/alpha', project_id: 'proj_alpha' };
const beta = { root: '/work/beta', project_id: 'proj_beta' };
const task = { id: 'task_01', title: 'Persisted integration task', status: 'in_progress', created_at: time, updated_at: time };
const checkpoint = { id: 'cp_01', task_id: task.id, session_id: 'sess_01', kind: 'manual', created_at: time };
const handoff = { id: 'handoff_01', task_id: task.id, source_provider_id: 'codex', target_provider_id: 'claude', status: 'delivered', created_at: time };
const session = {
  id: 'sess_01', task_id: task.id, provider_id: 'codex', status: 'running', started_at: time,
  ended_at: null, reconciled_at: null, resumed_from_session_id: null, exit_reason: null,
  exit_code: null, ownership_uncertain: true, native_resume: false, task_continuation: true,
  checkpoint, delivery: { id: handoff.id, status: handoff.status, delivered_at: time, failure_code: null },
};
const content = (items: string[] = []) => ({ items, total: items.length, truncated: false });
const overview = {
  ...alpha, name: 'Alpha workspace', schema_version: 6, active_task: task,
  latest_session: session, active_has_sessions: true, workspace_busy: null, unfinalized_sessions: 1,
  current_work: 'Read state', completed: content(['Verified identity']), issues: content(), checkpoint,
};
const pages = { items: [checkpoint], next: null };
let entries: (typeof alpha)[];
let openChoice: typeof alpha | null;
let queryError: string | null;
let overviewResult: typeof overview | (Omit<typeof overview, 'workspace_busy' | 'latest_session'> &
  { workspace_busy: boolean; latest_session: typeof session | null });
let pickedObjective: { name: string; text: string } | null;
let actionOutcome: object | null;
let submitFailed: boolean;
const objective = '# Objective\n\n<iframe src="https://example.org">unsafe</iframe>\n' + '🧪'.repeat(8_100);

beforeEach(() => {
  vi.stubGlobal('localStorage', jsdom.window.localStorage);
  // jsdom does not implement the native modal methods used by WKWebView and WebView2.
  Object.defineProperties(HTMLDialogElement.prototype, {
    showModal: { configurable: true, value(this: HTMLDialogElement) { this.open = true; } },
    close: { configurable: true, value(this: HTMLDialogElement) { this.open = false; } },
  });
  window.localStorage.clear();
  entries = [];
  openChoice = alpha;
  queryError = null;
  overviewResult = overview;
  pickedObjective = null;
  actionOutcome = null;
  submitFailed = false;
  tauri.invoke.mockReset();
  tauri.invoke.mockImplementation(async (command: string, input?: { request?: { op: string; root?: string; offset?: number } }) => {
    if (command === 'library') return entries.slice();
    if (command === 'open_project') {
      if (openChoice) entries.push(openChoice);
      return openChoice;
    }
    if (command === 'remove_project') {
      entries = entries.filter(entry => entry.root !== (input as { root?: string } | undefined)?.root);
      return entries;
    }
    if (command === 'pick_objective') return pickedObjective;
    if (command === 'create_task') return { id: task.id, active: true };
    if (queryError) throw { message: queryError };
    const request = input?.request;
    switch (request?.op) {
      case 'action_status': return { action: actionOutcome, workspace_busy: overviewResult.workspace_busy };
      case 'providers': return { terminal: true, providers: [
        { id: 'claude', installed: true, managed: true },
        { id: 'codex', installed: true, managed: true },
      ] };
      case 'plan': return { provider: 'codex', mode: 'task_continuation', source: session.id, target: null, bootstrap_turn: false };
      case 'submit':
        if (submitFailed) throw { code: 'bridge_timeout' };
        return { action: actionOutcome };
      case 'session': return { ...session, objective_receipt: { state: 'partial', served: 4_000, total: 8_100 } };
      case 'hello': return { protocol: 1, core_version: '0.1.0' };
      case 'overview': return request.root === beta.root
        ? { ...overview, ...beta, name: 'Beta workspace', active_task: null } : overviewResult;
      case 'tasks': return { items: [{ ...task, objective_preview: 'Stored objective preview', session_count: 1 }], next: null };
      case 'task': return { ...task, requirements: content(['Read canonical data']), constraints: content(),
        completed: content(), current_work: null, remaining: content(), issues: content(), decisions: content(['Use WAL']) };
      case 'objective': return request.offset ? { text: 'Last objective page', next: null } : { text: objective, next: 8192 };
      case 'sessions': return { items: [session], next: null };
      case 'checkpoints': return pages;
      case 'handoffs': return { items: [handoff], next: null };
      case 'checkpoint': return { ...checkpoint, decisions: content(['Use WAL']), remaining: content(), issues: content(),
        operator_note: null, observed_head: null, test_status: { known: true, summary: 'Reported checks', provenance: 'reported' } };
      case 'handoff': return { ...handoff, decisions: content(), remaining: content(), issues: content(), failure_code: null,
        next_action: 'Continue reviewing', test_status: { known: false, summary: 'Unknown', provenance: 'unknown' } };
      default: throw Error('Unexpected command in frontend test');
    }
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('shows onboarding and opens a project with the keyboard', async () => {
  const user = userEvent.setup();
  render(<App />);
  expect(screen.getByRole('status').textContent).toMatch(/Loading project library/);
  const button = await screen.findByRole('button', { name: 'Open Project' });
  expect(screen.getByRole('heading', { level: 1 }).textContent).toMatch(/Keep the context/);
  await user.tab();
  expect(document.activeElement).toBe(screen.getByRole('link', { name: 'Skip to content' }));
  button.focus();
  await user.keyboard('{Enter}');
  expect(await screen.findByRole('heading', { name: 'Alpha workspace' })).toBeTruthy();
  expect(screen.getByText('Ownership unknown')).toBeTruthy();
});

it('switches registered workspaces and removes only the library entry', async () => {
  const user = userEvent.setup();
  entries = [alpha, beta];
  render(<App />);
  await screen.findByRole('heading', { name: 'Alpha workspace' });
  await user.click(screen.getByRole('button', { name: 'beta' }));
  expect(await screen.findByRole('heading', { name: 'Beta workspace' })).toBeTruthy();
  expect(screen.getByText('No active Task')).toBeTruthy();
  await user.click(screen.getByRole('button', { name: 'Remove project from library' }));
  expect(tauri.invoke).toHaveBeenCalledWith('remove_project', { root: beta.root });
});

it('pages long Markdown as inert text and opens Task-scoped Session checkpoint details', async () => {
  const user = userEvent.setup();
  entries = [alpha];
  render(<App />);
  const nav = await screen.findByRole('navigation', { name: 'Workspace' });
  await user.click(within(nav).getByRole('button', { name: 'Tasks' }));
  await user.click(await screen.findByRole('button', { name: /Persisted integration task/ }));
  await screen.findByRole('heading', { name: task.title });
  const objectivePanel = screen.getByRole('heading', { name: 'Objective' }).closest('section')!;
  await within(objectivePanel).findByRole('button', { name: 'Next' });
  expect(objectivePanel.querySelector('pre')?.textContent).toBe(objective);
  expect(objectivePanel.querySelector('iframe')).toBeNull();
  await user.click(within(objectivePanel).getByRole('button', { name: 'Next' }));
  expect(await screen.findByText('Last objective page')).toBeTruthy();
  await user.click(within(screen.getByRole('heading', { name: task.title }).closest('header')!).getByRole('button', { name: 'Sessions' }));
  await user.click(await screen.findByRole('button', { name: /Codex.*Ownership uncertain/ }));
  expect(screen.getByText('Task-level continuation only')).toBeTruthy();
  expect(tauri.invoke).toHaveBeenCalledWith('query', { request: { op: 'sessions', root: alpha.root, task_id: task.id, offset: 0 } });
  await user.click(screen.getByRole('button', { name: 'Manual' }));
  expect(await screen.findByRole('heading', { name: 'Manual checkpoint' })).toBeTruthy();
  expect(screen.getByText('Reported · Unverified').getAttribute('data-tone')).toBe('warning');
  expect(screen.getByText('Historical observation').closest('[data-tone]')).toBeNull();
});

it('shows persisted handoff details', async () => {
  const user = userEvent.setup();
  entries = [alpha];
  render(<App />);
  await user.click(await screen.findByRole('button', { name: 'Checkpoints & handoffs' }));
  await user.click(screen.getByRole('button', { name: 'Handoffs' }));
  await user.click(await screen.findByRole('button', { name: /Codex → Claude Code/ }));
  expect(await screen.findByRole('heading', { name: 'Handoff record' })).toBeTruthy();
  expect(screen.getByText('Continue reviewing')).toBeTruthy();
});

it('reports errors and refreshes from the bridge', async () => {
  const user = userEvent.setup();
  entries = [alpha];
  queryError = 'Project state is busy. Refresh to try again.';
  render(<App />);
  expect((await screen.findByRole('alert')).textContent).toContain(queryError);
  queryError = null;
  await user.click(screen.getByRole('button', { name: 'Refresh workspace' }));
  expect(await screen.findByRole('heading', { name: 'Alpha workspace' })).toBeTruthy();
});

it('uses system appearance by default and persists an explicit theme', async () => {
  const user = userEvent.setup();
  const view = render(<App />);
  await user.click(screen.getByRole('button', { name: 'Settings & diagnostics' }));
  const appearance = screen.getByRole<HTMLSelectElement>('combobox', { name: 'Theme' });
  expect(appearance.value).toBe('system');
  await user.selectOptions(appearance, 'dark');
  expect(document.documentElement.dataset.theme).toBe('dark');
  expect((await screen.findByText('Ready · Local stdio')).getAttribute('data-tone')).toBe('success');
  view.unmount();
  render(<App />);
  expect(document.documentElement.dataset.theme).toBe('dark');
  await user.click(screen.getByRole('button', { name: 'Settings & diagnostics' }));
  const savedAppearance = screen.getByRole<HTMLSelectElement>('combobox', { name: 'Theme' });
  await user.selectOptions(savedAppearance, 'light');
  expect(document.documentElement.dataset.theme).toBe('light');
  await user.selectOptions(savedAppearance, 'system');
  expect(document.documentElement.dataset.theme).toBe('system');
});

it('keeps onboarding when the native picker is cancelled', async () => {
  const user = userEvent.setup();
  openChoice = null;
  render(<App />);
  await user.click(await screen.findByRole('button', { name: 'Open Project' }));
  expect(screen.getByRole('heading', { level: 1 }).textContent).toMatch(/Keep the context/);
  expect(screen.queryByRole('alert')).toBeNull();
});

it('opens New Task from the keyboard, handles picker cancellation and keeps the Markdown source exact', async () => {
  const user = userEvent.setup();
  entries = [alpha];
  overviewResult = { ...overview, workspace_busy: false, unfinalized_sessions: 0 };
  render(<App />);
  const button = await screen.findByRole('button', { name: 'New Task' });
  button.focus();
  await user.keyboard('{Enter}');
  const dialog = screen.getByRole('dialog', { name: 'New Task' });
  const title = within(dialog).getByRole('textbox', { name: 'Title' });
  expect(document.activeElement).toBe(title);
  await user.type(title, 'Markdown continuity');
  await user.click(within(dialog).getByRole('button', { name: 'Choose Markdown file' }));
  expect(within(dialog).getByRole('textbox', { name: 'Objective' })).toBeTruthy();
  pickedObjective = { name: 'Objective.md', text: '  # Unicode 🧪\r\n  ' };
  await user.click(within(dialog).getByRole('button', { name: 'Choose Markdown file' }));
  expect(await within(dialog).findByText('Objective.md')).toBeTruthy();
  expect(dialog.querySelector('pre')?.textContent).toBe(pickedObjective.text);
  await user.click(within(dialog).getByRole('checkbox', { name: 'Make active' }));
  const submit = within(dialog).getByRole('button', { name: 'Create Task' });
  expect((submit as HTMLButtonElement).disabled).toBe(true);
  await user.click(within(dialog).getByRole('checkbox', { name: /keep the previous Task/ }));
  await user.click(submit);
  await screen.findByText('Task saved.');
  expect(tauri.invoke).toHaveBeenCalledWith('create_task', { request: expect.objectContaining({
    root: alpha.root, title: 'Markdown continuity', objective: pickedObjective.text,
    activate: true, expected_active: task.id,
  }) });
});

it('displays the core fresh-continuation plan and gives an indeterminate launch a safe next step', async () => {
  const user = userEvent.setup();
  entries = [alpha];
  submitFailed = true;
  overviewResult = { ...overview, workspace_busy: false, unfinalized_sessions: 0 };
  render(<App />);
  await user.click(await screen.findByRole('button', { name: 'Resume' }));
  const dialog = screen.getByRole('dialog', { name: 'Resume work' });
  expect(await within(dialog).findByText('Continue the Task in a fresh conversation')).toBeTruthy();
  await user.click(within(dialog).getByRole('button', { name: 'Open terminal' }));
  expect(await screen.findByText(/bridge lost contact during submission/)).toBeTruthy();
  expect(tauri.invoke.mock.calls.filter(([, input]) => input?.request?.op === 'submit')).toHaveLength(1);
});

it('shows the objectively served prefix for a Session reached from the action receipt', async () => {
  const user = userEvent.setup();
  entries = [alpha];
  actionOutcome = { id: 'job_a', phase: 'running', provider: 'codex', mode: 'start', session_id: session.id };
  render(<App />);
  const receipt = await screen.findByText('Codex · Native provider launched');
  expect(receipt).toBeTruthy();
  await user.click(await screen.findByRole('button', { name: 'View Session' }));
  expect(await screen.findByText('MCP served part of objective')).toBeTruthy();
  expect(screen.getByText('4000 of 8100 Unicode characters served')).toBeTruthy();
});
