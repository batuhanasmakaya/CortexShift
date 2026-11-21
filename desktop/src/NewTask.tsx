import { FileText, LoaderCircle, Plus } from 'lucide-react';
import { useCallback, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { agentAction, Dialog, managed, providerUnavailable } from './AgentControl';
import { desktop, errorMessage, operationId, provider, uncertainTransport } from './ipc';
import type { AgentAction, ManagedProvider, ObjectiveFile, Task } from './ipc';
import { useRead } from './read';
import { ErrorNotice } from './ui';

export function NewTask({ root, active, blocked, close, saved, observe }: {
  root: string; active: Task | null; blocked: string | null; close: () => void;
  saved: (taskId: string, launch?: AgentAction, message?: string) => void;
  observe: () => void;
}) {
  const [taskId] = useState(() => 'task_' + operationId());
  const [title, setTitle] = useState('');
  const [text, setText] = useState('');
  const [file, setFile] = useState<ObjectiveFile>();
  const [destination, setDestination] = useState<ManagedProvider>('claude');
  const [activate, setActivate] = useState(!active);
  const [replace, setReplace] = useState(false);
  const [busy, setBusy] = useState(false);
  const [picking, setPicking] = useState(false);
  const [failure, setFailure] = useState<string>();
  const [uncertain, setUncertain] = useState(false);
  const wait = useRef(false);
  const availability = useRead(useCallback(() => desktop.control('providers', root, {}), [root]));
  const objective = file?.text ?? text;
  const valid = title.trim().length > 0 && objective.trim().length > 0 && (!activate || !active || replace);
  const launchBlock = blocked ?? (availability.state === 'ready'
    ? providerUnavailable(availability.value, destination) : availability.state === 'error' ? availability.message : 'Checking provider availability…');

  async function pick() {
    setPicking(true);
    setFailure(undefined);
    try {
      const selected = await desktop.pickObjective();
      if (selected) setFile(selected);
    } catch (error) { setFailure(errorMessage(error)); }
    finally { setPicking(false); }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const start = (event.nativeEvent as SubmitEvent).submitter?.getAttribute('value') === 'start';
    if (!valid || (start && launchBlock !== null) || wait.current) return;
    wait.current = true;
    setBusy(true);
    setFailure(undefined);
    try {
      const result = await desktop.createTask({
        root, task_id: taskId, title, objective, activate: start || activate,
        expected_active: active?.id ?? null,
      });
      saved(result.id, start && result.active ? agentAction('start', result.id, destination) : undefined,
        start && !result.active ? 'Task saved. Activate it before starting a provider.' : 'Task saved.');
    } catch (error) {
      if (uncertainTransport(error)) {
        setUncertain(true);
        setFailure('Task creation outcome is uncertain. Check the saved Task or retry with the same creation ID.');
        observe();
      } else {
        setFailure(errorMessage(error));
      }
    } finally {
      setBusy(false);
      wait.current = false;
    }
  }

  return <Dialog title="New Task" busy={busy || picking} close={close}>
    <form className="control-form" onSubmit={event => void submit(event)}>
      {failure && <ErrorNotice message={failure} />}
      <label className="control-field">Title
        <input data-initial-focus value={title} onChange={event => setTitle(event.target.value)} maxLength={1024}
          required disabled={busy || uncertain} placeholder="Give this Task a name" />
      </label>
      <div className="objective-tools">
        <span className="field-label">Objective</span>
        {file ? <button type="button" className="text-button" disabled={busy || uncertain}
          onClick={() => setFile(undefined)}>Use typed objective</button>
          : <button type="button" className="button" disabled={busy || picking || uncertain}
            onClick={() => void pick()}><FileText /> {picking ? 'Choosing file…' : 'Choose Markdown file'}</button>}
      </div>
      {file ? <div className="file-preview" aria-label="Markdown preview">
        <strong>{file.name}</strong><pre>{file.text.slice(0, 8192)}</pre>
        {file.text.length > 8192 && <p className="muted">Preview shortened. The complete file is saved.</p>}
      </div> : <textarea aria-label="Objective" value={text} onChange={event => setText(event.target.value)}
          rows={5} required disabled={busy || uncertain} placeholder="Describe the work, requirements, and constraints." />}
      <div className="control-columns">
        <label className="control-field">Provider for launch
          <select value={destination} disabled={busy || uncertain}
            onChange={event => setDestination(event.target.value as ManagedProvider)}>
            {managed.map(id => <option key={id} value={id}>{provider(id)}</option>)}
          </select>
        </label>
        <label className="control-check"><input type="checkbox" checked={activate} disabled={busy || uncertain}
          onChange={event => { setActivate(event.target.checked); setReplace(false); }} /> Make active</label>
      </div>
      {active && <div className="active-task-choice">
        <p className="muted">Active Task · {active.title}</p>
        {activate && <label className="control-check"><input type="checkbox" checked={replace} disabled={busy || uncertain}
          onChange={event => setReplace(event.target.checked)} /> Make this Task active and keep the previous Task in history</label>}
      </div>}
      {launchBlock && <p className="muted">{launchBlock}</p>}
      <footer className="dialog-actions">
        <button type="button" className="button" disabled={busy || picking} onClick={close}>Cancel</button>
        <button type="submit" className={activate && !launchBlock ? 'button' : 'button primary'}
          disabled={busy || picking || !valid}>{busy ? <LoaderCircle className="spin" /> : <Plus />}
          {uncertain ? 'Check or retry creation' : 'Create Task'}</button>
        {activate && !uncertain && <button type="submit" value="start" className="button primary"
          disabled={busy || picking || !valid || launchBlock !== null}>Create &amp; start with {provider(destination)}</button>}
      </footer>
    </form>
  </Dialog>;
}
