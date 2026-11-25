import { useState } from 'react';
import { desktop, errorMessage } from './ipc';
import type { UpdateOffer, UpdateProgress } from './ipc';
import { useRead } from './read';
import { ErrorNotice } from './ui';

export function DesktopUpdates() {
  const info = useRead(desktop.updateInfo);
  const [offer, setOffer] = useState<UpdateOffer | null>();
  const [operation, setOperation] = useState<'check' | 'install'>();
  const [progress, setProgress] = useState<UpdateProgress>();
  const [error, setError] = useState<string>();
  const [installed, setInstalled] = useState(false);

  async function check() {
    setOperation('check');
    setError(undefined);
    setOffer(undefined);
    try {
      setOffer(await desktop.checkUpdate());
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setOperation(undefined);
    }
  }

  async function install() {
    setOperation('install');
    setError(undefined);
    setProgress(undefined);
    try {
      await desktop.installUpdate(setProgress);
      setInstalled(true);
    } catch (failure) {
      setError(errorMessage(failure));
      if (typeof failure === 'object' && failure !== null && 'code' in failure && failure.code === 'install_failed') {
        setOffer(undefined);
      }
    } finally {
      setOperation(undefined);
    }
  }

  return <section className="panel settings">
    <div className="panel-head"><h2>Desktop updates</h2></div>
    {info.state === 'loading' && <p role="status">Reading installed version…</p>}
    {info.state === 'error' && <ErrorNotice message={info.message} />}
    {info.state === 'ready' && <>
      <p>Installed version {info.value.version}</p>
      {!info.value.available ? <p className="muted">This build has no configured signed update channel.</p>
        : installed || info.value.restart_required ? <p role="status">Update installed. Quit and reopen CortexShift Desktop.</p>
          : <>
            <button className="button" disabled={operation !== undefined} onClick={() => void check()}>
              {operation === 'check' ? 'Checking…' : 'Check for updates'}
            </button>
            {offer === null && <p role="status">Desktop is up to date.</p>}
            {offer && <div className="detail-section">
              <h3>Version {offer.version} available</h3>
              {offer.notes && <pre className="objective">{offer.notes}</pre>}
              <p className="muted">Save your work and finish any running agents before installing. On Windows the installer closes Desktop.</p>
              <button className="button primary" disabled={operation !== undefined} onClick={() => void install()}>
                Download and install
              </button>
            </div>}
            {operation === 'install' && <div className="detail-section">
              <p role="status">{progress?.total && progress.received >= progress.total
                ? 'Verifying the download and checking workspace activity…' : 'Downloading signed update…'}</p>
              <progress aria-label="Update download" max={progress?.total ?? undefined}
                value={progress?.total ? progress.received : undefined} />
            </div>}
            {error && <ErrorNotice message={error} />}
          </>}
    </>}
  </section>;
}
