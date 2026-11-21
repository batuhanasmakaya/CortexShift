import { invoke } from '@tauri-apps/api/core';

export interface Project { project_id: string; root: string }
export interface Task {
  id: string;
  title: string;
  status: string;
  created_at: string;
  updated_at: string;
}
export interface TaskPreview extends Task { objective_preview: string; session_count: number }
export interface TextItems { items: string[]; total: number; truncated: boolean }
export interface TaskDetail extends Task {
  requirements: TextItems;
  constraints: TextItems;
  completed: TextItems;
  current_work: string | null;
  remaining: TextItems;
  issues: TextItems;
  decisions: TextItems;
}
export interface Checkpoint {
  id: string;
  task_id: string;
  session_id: string | null;
  kind: string;
  created_at: string;
}
export interface Handoff {
  id: string;
  task_id: string;
  source_provider_id: string;
  target_provider_id: string;
  status: string;
  created_at: string;
}
interface HistoricalDetail {
  decisions: TextItems;
  remaining: TextItems;
  issues: TextItems;
  test_status: { known: boolean; summary: string; provenance: string };
  operator_note: string | null;
  observed_head: string | null;
}
export type CheckpointDetail = Checkpoint & HistoricalDetail;
export type HandoffDetail = Handoff & HistoricalDetail & {
  source_checkpoint_id: string | null;
  delivered_at: string | null;
  failure_code: string | null;
  next_action: string;
};
export interface Session {
  id: string;
  task_id: string;
  provider_id: string;
  status: string;
  started_at: string;
  ended_at: string | null;
  reconciled_at: string | null;
  resumed_from_session_id: string | null;
  exit_reason: string | null;
  exit_code: number | null;
  ownership_uncertain: boolean;
  native_resume: boolean;
  task_continuation: boolean;
  checkpoint: Checkpoint | null;
  delivery: { id: string; status: string; delivered_at: string | null; failure_code: string | null } | null;
}
export interface Overview extends Project {
  name: string;
  schema_version: number;
  active_task: Task | null;
  latest_session: Session | null;
  checkpoint: Checkpoint | null;
  workspace_busy: boolean | null;
  unfinalized_sessions: number;
  current_work: string | null;
  completed: TextItems;
  issues: TextItems;
}
interface Page<T> { items: T[]; next: number | null }

interface Operations {
  hello: [Record<string, never>, { protocol: number; core_version: string }];
  overview: [{ root: string }, Overview];
  tasks: [{ root: string; offset: number }, Page<TaskPreview>];
  task: [{ root: string; task_id: string }, TaskDetail];
  objective: [{ root: string; task_id: string; offset: number }, { text: string; next: number | null }];
  sessions: [{ root: string; task_id: string | null; offset: number }, Page<Session>];
  checkpoints: [{ root: string; task_id: string | null; offset: number }, Page<Checkpoint>];
  handoffs: [{ root: string; task_id: string | null; offset: number }, Page<Handoff>];
  checkpoint: [{ root: string; record_id: string }, CheckpointDetail];
  handoff: [{ root: string; record_id: string }, HandoffDetail];
}

export const desktop = {
  library: () => invoke<Project[]>('library'),
  openProject: () => invoke<Project | null>('open_project'),
  removeProject: (root: string) => invoke<Project[]>('remove_project', { root }),
  query: <K extends keyof Operations>(op: K, params: Operations[K][0]) =>
    invoke<Operations[K][1]>('query', { request: { op, ...params } }),
};

export function errorMessage(error: unknown) {
  if (typeof error === 'object' && error !== null && 'message' in error &&
      typeof error.message === 'string') return error.message;
  return 'CortexShift Desktop could not complete this request. Try refreshing.';
}

export const folderName = (path: string) => path.split(/[/\\]/).filter(Boolean).at(-1) ?? path;
export const label = (value: string) => value.replaceAll('_', ' ').replace(/^./, c => c.toUpperCase());
const providers = new Map([['codex', 'Codex'], ['claude', 'Claude Code'], ['antigravity', 'Antigravity']]);
export const provider = (value: string) => providers.get(value) ?? label(value);
const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });
export function date(value: string | null) {
  if (value === null) return 'Unknown';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) return 'Unknown';
  return dateFormat.format(parsed);
}
