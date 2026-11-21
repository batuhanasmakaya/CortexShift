import { ArrowRight, LoaderCircle, Play, RotateCcw, SquareTerminal, X } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { desktop, errorMessage, operationId, provider, uncertainTransport } from './ipc';
import type { ActionReceipt, AgentAction, AgentPlan, ManagedProvider, Overview, ProviderAvailability, Session } from './ipc';
import { useRead } from './read';
import { ErrorNotice, ReadView, StatusIndicator } from './ui';

export const managed = ['claude', 'codex'] as const;
export const actionPending = (receipt: ActionReceipt | null) =>
  receipt !== null && ['pending', 'starting', 'context_prepared', 'running'].includes(receipt.phase);

export function agentAction(kind: AgentAction['kind'], taskId: string,
  destination: ManagedProvider | null = null, sessionId: string | null = null): AgentAction {
  return { kind, task_id: taskId, provider: destination, session_id: sessionId, fresh_target: false };
}

export function useActionStatus(root: string | undefined, changed: () => void) {
  const [snapshot, setSnapshot] = useState<{ root: string; receipt: ActionReceipt | null }>();
  const [revision, setRevision] = useState(0);
  const [observationFailed, setObservationFailed] = useState(false);
  const previous = useRef<{ root: string; identity: string } | undefined>(undefined);
  const check = useCallback(() => setRevision(value => value + 1), []);

  useEffect(() => {
    if (!root) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function read(path: string) {
      if (document.visibilityState !== 'hidden') {
        try {
          const { action: receipt, workspace_busy } = await desktop.control('action_status', path, {});
          if (!active) return;
          const identity = `${workspace_busy}:${receipt?.id ?? ''}:${receipt?.phase ?? ''}:${receipt?.objective_served ?? ''}`;
          if (previous.current?.root === path && previous.current.identity !== identity) changed();
          previous.current = { root: path, identity };
          setSnapshot({ root: path, receipt });
          setObservationFailed(false);
        } catch {
          if (active) setObservationFailed(true);
        }
      }
      if (active) timer = setTimeout(() => void read(path), 3500);
    }
    void read(root);
    return () => { active = false; clearTimeout(timer); };
  }, [root, revision, changed]);

  return {
    receipt: snapshot?.root === root ? snapshot?.receipt ?? null : null,
    observationFailed,
    check,
  };
}

export function Dialog({ title, busy, close, children }: {
  title: string; busy: boolean; close: () => void; children: ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current;
    const trigger = document.activeElement;
    element?.showModal();
    element?.querySelector<HTMLElement>('[data-initial-focus]')?.focus();
    return () => {
      element?.close();
      if (trigger instanceof HTMLElement && trigger.isConnected) trigger.focus();
    };
  }, []);
  return <dialog ref={dialog} className="control-dialog" aria-labelledby="control-title"
    onCancel={event => { event.preventDefault(); if (!busy) close(); }}>
    <header className="dialog-heading"><h2 id="control-title">{title}</h2>
      <button type="button" className="icon-button" aria-label="Close dialog" disabled={busy} onClick={close}><X /></button>
    </header>
    {children}
  </dialog>;
}

export function providerUnavailable(availability: ProviderAvailability, id: ManagedProvider) {
  if (!availability.terminal) return 'Native terminal launch unavailable';
  const entry = availability.providers.find(item => item.id === id);
  if (!entry?.managed) return 'Managed startup unavailable';
  return entry.installed ? null : 'Executable unavailable';
}

export function OverviewControls({ data, launch, blocked }: {
  data: Overview; launch: (action: AgentAction) => void; blocked: string | null;
}) {
  const availability = useRead(useCallback(() => desktop.control('providers', data.root, {}), [data.root]));
  const task = data.active_task;
  if (!task || ['completed', 'cancelled'].includes(task.status)) return null;
  const hasSessions = data.active_has_sessions;
  return <section className="panel agent-controls" aria-label="Agent controls">
    <div className="panel-head"><h2>Agent controls</h2><SquareTerminal /></div>
    <ReadView read={availability}>{result => {
      const available = managed.some(id => providerUnavailable(result, id) === null);
      return <>
        <div className="actions">
          {hasSessions && <button className="button primary" disabled={Boolean(blocked) || !available}
            onClick={() => launch(agentAction('resume', task.id))}><RotateCcw /> Resume</button>}
          {managed.filter(id => !hasSessions || id !== data.active_recent_provider).map(id => {
            const reason = providerUnavailable(result, id);
            return <button key={id} className={hasSessions ? 'button' : 'button primary'}
              disabled={Boolean(blocked || reason)} title={blocked ?? reason ?? undefined}
              onClick={() => launch(agentAction(hasSessions ? 'switch' : 'start', task.id, id))}>
              {hasSessions ? <ArrowRight /> : <Play />}{hasSessions ? 'Switch to' : 'Start with'} {provider(id)}
            </button>;
          })}
          {hasSessions && <button className="button" disabled={Boolean(blocked) || !available}
            onClick={() => launch(agentAction('start', task.id, managed.find(id => !providerUnavailable(result, id)) ?? null))}>
            Start fresh
          </button>}
        </div>
        {blocked ? <p className="muted">{blocked}</p> : <p className="muted">
          {managed.map(id => `${provider(id)} · ${providerUnavailable(result, id) ?? 'Executable available'}`).join('   /   ')}
        </p>}
      </>;
    }}</ReadView>
  </section>;
}

export function SessionControls({ session, activeId, blocked, launch, recover }: {
  session: Session; activeId: string | null; blocked: string | null;
  launch: (action: AgentAction) => void; recover: () => void;
}) {
  if (session.ownership_uncertain) return <div className="session-actions">
    <button className="button" disabled={blocked !== null && !blocked.includes('unfinalized')}
      onClick={recover}>Review recovery</button>
    <p className="muted">{blocked ?? 'Review ownership before continuing this Session.'}</p>
  </div>;
  if (session.task_id !== activeId) return <p className="muted">Activate this Task to continue its Sessions.</p>;
  const destination = managed.find(id => id !== session.provider_id) ?? 'claude';
  return <div className="session-actions">
    <div className="actions">
      {session.native_resume && <button className="button" disabled={!!blocked}
        onClick={() => launch(agentAction('resume', session.task_id, null, session.id))}>Resume this Session</button>}
      {session.task_continuation && <button className="button" disabled={!!blocked}
        onClick={() => launch(agentAction('continue', session.task_id, null, session.id))}>Continue Task fresh</button>}
      <button className="button" disabled={!!blocked}
        onClick={() => launch(agentAction('switch', session.task_id, destination, session.id))}>Switch to {provider(destination)}</button>
      {!managed.includes(session.provider_id as ManagedProvider) && destination !== 'codex' &&
        <button className="button" disabled={!!blocked}
          onClick={() => launch(agentAction('switch', session.task_id, 'codex', session.id))}>Switch to Codex</button>}
    </div>
    {blocked && <p className="muted">{blocked}</p>}
  </div>;
}

const modeLabels: Record<AgentPlan['mode'], string> = {
  start: 'Start a new conversation',
  native_resume: 'Request exact native resume',
  task_continuation: 'Continue the Task in a fresh conversation',
  switch_native: 'Switch and request exact native resume',
  switch_fresh: 'Switch into a fresh conversation',
};

export function SetupCheck({ root, id }: { root: string; id: ManagedProvider }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string>();
  async function check() {
    setBusy(true);
    setResult(undefined);
    try {
      const feedback = await desktop.control('provider_setup', root, { provider: id });
      setResult(!feedback.installed ? 'Executable unavailable.'
        : feedback.authentication === 'not_authenticated' ? 'Setup incomplete. Sign in through the native CLI.'
          : feedback.authentication === 'authenticated' ? 'Native CLI reports that it is signed in.'
            : 'Sign-in status could not be verified. Check the native CLI.');
    } catch (error) { setResult(errorMessage(error)); }
    finally { setBusy(false); }
  }
  return <div className="setup-check">
    <button type="button" className="text-button" disabled={busy} onClick={() => void check()}>
      {busy ? 'Checking setup…' : 'Check setup'}
    </button>
    {result && <p role="status" className="muted">{result}</p>}
  </div>;
}

export function LaunchDialog({ root, initialAction, close, submitted }: {
  root: string; initialAction: AgentAction; close: () => void;
  submitted: (message?: string) => void;
}) {
  const [action, setAction] = useState(initialAction);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string>();
  const availability = useRead(useCallback(() => desktop.control('providers', root, {}), [root]));
  const plan = useRead(useCallback(() => desktop.control('plan', root, { action }), [root, action]));
  const wait = useRef(false);

  async function launch(selected: AgentPlan) {
    if (wait.current) return;
    wait.current = true;
    setBusy(true);
    setFailure(undefined);
    try {
      await desktop.control('submit', root, { action, plan: selected, operation_id: operationId() });
      submitted();
    } catch (error) {
      if (uncertainTransport(error)) {
        submitted('The bridge lost contact during submission. Check Desktop action status and Session history before retrying.');
      } else {
        setFailure(errorMessage(error));
      }
    } finally {
      setBusy(false);
      wait.current = false;
    }
  }

  return <Dialog title={action.kind === 'switch' ? 'Switch provider' : action.kind === 'start' ? 'Start agent' : 'Resume work'}
    busy={busy} close={close}>
    {failure && <ErrorNotice message={failure} />}
    {(action.kind === 'switch' || action.kind === 'start') && <label className="control-field">
      Provider
      <select value={action.provider ?? ''} disabled={busy}
        onChange={event => setAction({ ...action, provider: event.target.value as ManagedProvider })}>
        {managed.map(id => <option key={id} value={id}>{provider(id)}
          {availability.state === 'ready' && providerUnavailable(availability.value, id) ? ' · Unavailable' : ''}</option>)}
      </select>
    </label>}
    {action.kind === 'switch' && <label className="control-check">
      <input type="checkbox" checked={action.fresh_target} disabled={busy}
        onChange={event => setAction({ ...action, fresh_target: event.target.checked })} /> Open a fresh destination conversation
    </label>}
    <ReadView read={plan}>{selected => {
      const unavailable = availability.state === 'ready' && providerUnavailable(availability.value, selected.provider);
      return <>
        <div className="plan-summary">
          <StatusIndicator>{provider(selected.provider)}</StatusIndicator>
          <strong>{modeLabels[selected.mode]}</strong>
          <p className="muted">The provider opens in a native terminal for this workspace.</p>
          {action.kind === 'switch' && <p className="muted">CortexShift prepares a handoff from the Task and recorded progress.</p>}
          {selected.bootstrap_turn && <p className="muted">The destination integration requests a provider bootstrap turn before opening its terminal session.</p>}
          {unavailable ? <p className="muted">{unavailable}</p>
            : <SetupCheck key={selected.provider} root={root} id={selected.provider} />}
        </div>
        <footer className="dialog-actions">
          <button className="button" disabled={busy} onClick={close}>Cancel</button>
          <button className="button primary" disabled={busy || Boolean(unavailable) || availability.state !== 'ready'}
            onClick={() => void launch(selected)}>
            {busy ? <LoaderCircle className="spin" /> : <SquareTerminal />}{busy ? 'Opening terminal…' : 'Open terminal'}
          </button>
        </footer>
      </>;
    }}</ReadView>
  </Dialog>;
}

const phases: Record<ActionReceipt['phase'], string> = {
  pending: 'Waiting for native terminal',
  starting: 'Preparing managed invocation',
  context_prepared: 'Handoff context prepared',
  running: 'Native provider launched',
  completed: 'Invocation finished',
  failed: 'Invocation failed',
  interrupted: 'Invocation interrupted',
  unknown: 'Invocation outcome uncertain',
  cancelled: 'Pending launch cancelled',
};

export function ActionBanner({ root, receipt, observeFailed, change, openSession }: {
  root: string; receipt: ActionReceipt; observeFailed: boolean;
  change: (message?: string) => void; openSession: (id: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  async function cancel() {
    setBusy(true);
    try { await desktop.control('cancel_action', root, { action_id: receipt.id }); change(); }
    catch (error) { change(errorMessage(error)); }
    finally { setBusy(false); }
  }
  const warning = observeFailed || ['unknown', 'interrupted', 'failed'].includes(receipt.phase);
  return <div className="action-banner notice" data-tone={warning ? 'warning' : undefined}>
    <div role="status"><strong>{provider(receipt.provider)} · {observeFailed ? 'Action status unavailable' : phases[receipt.phase]}</strong>
      {receipt.spawned && receipt.phase !== 'running' && <p>Native provider launch recorded.</p>}
      {receipt.message && <p>{receipt.message}</p>}
      {receipt.phase === 'unknown' && <p>Check workspace activity and Session history before retrying.</p>}
    </div>
    <div className="actions">
      {receipt.phase === 'pending' && <button className="button" disabled={busy || observeFailed}
        onClick={() => void cancel()}>Cancel pending launch</button>}
      {receipt.session_id && <button className="text-button" onClick={() => openSession(receipt.session_id!)}>View Session <ArrowRight /></button>}
    </div>
  </div>;
}

export function RecoveryDialog({ root, close, complete }: {
  root: string; close: () => void; complete: (message?: string) => void;
}) {
  const preview = useRead(useCallback(() => desktop.control('recover', root, { preview: true }), [root]));
  const [busy, setBusy] = useState(false);
  async function recover() {
    setBusy(true);
    try {
      const result = await desktop.control('recover', root, { preview: false });
      complete(`Recovery reconciled ${result.reconciled} invocation(s). ${result.uncertain} have uncertain ownership.`);
    } catch (error) { complete(uncertainTransport(error)
      ? 'Recovery outcome is uncertain. Refresh and inspect the project Sessions before retrying.' : errorMessage(error)); }
    finally { setBusy(false); }
  }
  return <Dialog title="Review recovery" busy={busy} close={close}>
    <ReadView read={preview}>{result => <>
      <p>This workspace has {result.eligible} eligible and {result.uncertain} uncertain invocation(s).</p>
      <p className="muted">Recovery reconciles eligible invocations across the project and records historical checkpoints. Invocations with uncertain ownership require review.</p>
      <footer className="dialog-actions">
        <button className="button" disabled={busy} onClick={close}>Close</button>
        <button className="button primary" disabled={busy || result.eligible === 0}
          onClick={() => void recover()}>{busy ? 'Reconciling…' : 'Recover eligible Sessions'}</button>
      </footer>
    </>}</ReadView>
  </Dialog>;
}
