import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { useApi } from "../../api/context";
import type { DiscoveryPlugin } from "../../api/types";
import {
  ACCESS_LABELS,
  type AccessChoice,
  type SourceForm,
  type TunnelAuth,
  emptyForm,
  endpointPlugins,
  fromSource,
  sourceHelp,
  toWrite,
  validateForm,
} from "../../model/sources";
import { ErrorLine } from "../shell/parts";
import { useLoad } from "../shell/useLoad";

/**
 * New or edit. The plugin list and each plugin's `source` wording come from
 * `GET /api/discovery/plugins` (C1.1): a new http plugin shows up here with no
 * UI change. The access block is the one part that is not schema-driven --
 * how a sensor is REACHED is the control plane's business (A7), not a plugin's.
 */
export function SourceEditor() {
  const api = useApi();
  const navigate = useNavigate();
  const { id } = useParams();
  const editing = Boolean(id);
  const plugins = useLoad((signal) => api.listPlugins(signal), []);
  const existing = useLoad(
    (signal) => (id ? api.getSource(id, signal) : Promise.resolve(null)),
    [id],
  );
  const [form, setForm] = useState<SourceForm>(() => emptyForm());
  const [seeded, setSeeded] = useState(false);
  const [problems, setProblems] = useState<string[]>([]);
  const [serverError, setServerError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);

  const usable = useMemo(() => endpointPlugins(plugins.data?.plugins ?? []), [plugins.data]);

  useEffect(() => {
    if (seeded) return;
    if (editing) {
      if (existing.data) {
        setForm(fromSource(existing.data));
        setSeeded(true);
      }
      return;
    }
    if (usable.length > 0) {
      setForm((f) => (f.plugin ? f : { ...f, plugin: usable[0]?.name ?? "" }));
      setSeeded(true);
    }
  }, [editing, existing.data, usable, seeded]);

  const plugin: DiscoveryPlugin | undefined = usable.find((p) => p.name === form.plugin);
  const set = <K extends keyof SourceForm>(key: K, value: SourceForm[K]) => setForm((f) => ({ ...f, [key]: value }));
  const setSsh = (key: keyof SourceForm["ssh"], value: string) => setForm((f) => ({ ...f, ssh: { ...f.ssh, [key]: value } }));
  const setDef = (key: keyof SourceForm["defaults"], value: string) =>
    setForm((f) => ({ ...f, defaults: { ...f.defaults, [key]: value } }));
  const auth = form.access === "ssh_tunnel" ? form.tunnelAuth : form.access;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const found = validateForm(form, editing);
    setProblems(found);
    setServerError(null);
    if (found.length) return;
    setSaving(true);
    try {
      const body = toWrite(form, editing);
      const saved = editing && id ? await api.updateSource(id, body) : await api.createSource(body);
      navigate(`/discovery/sources/${encodeURIComponent(saved.id)}`);
    } catch (err) {
      setServerError(err);
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <h1>{editing ? "Edit source" : "New discovery source"}</h1>
      <ErrorLine error={plugins.error} prefix="could not list plugins" />
      <ErrorLine error={existing.error} prefix="could not load the source" />
      {plugins.data && usable.length === 0 ? (
        <div className="empty">
          No installed discovery plugin reads from a network endpoint, so there is nothing a saved source
          could reach. Plugins installed: {(plugins.data.plugins.map((p) => p.name).join(", ") || "none")}.
        </div>
      ) : null}
      <form onSubmit={submit} className="panel" aria-label="Discovery source">
        <div className="fields-2">
          <div className="field">
            <label htmlFor="src-name">Name</label>
            <input id="src-name" type="text" value={form.name} onChange={(e) => set("name", e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="src-plugin">Plugin</label>
            <select id="src-plugin" value={form.plugin} onChange={(e) => set("plugin", e.target.value)}>
              {usable.map((p) => (
                <option key={p.name} value={p.name}>
                  {p.name}
                </option>
              ))}
            </select>
            <span className="hint">{sourceHelp(plugin)}</span>
          </div>
        </div>
        <div className="field">
          <label htmlFor="src-endpoint">Endpoint</label>
          <input
            id="src-endpoint"
            type="text"
            placeholder="http://127.0.0.1:9090"
            value={form.endpoint}
            onChange={(e) => set("endpoint", e.target.value)}
          />
          <span className="hint">
            The API's base URL as seen from where the access path lands. Through an SSH tunnel that is the
            SSH host's own view, e.g. <code>http://127.0.0.1:9090</code> for a sensor's loopback port. A bare
            host or IP is refused; give the scheme and port.
          </span>
        </div>

        <fieldset>
          <legend>Access</legend>
          <div className="choices" role="radiogroup" aria-label="Access">
            {(Object.keys(ACCESS_LABELS) as AccessChoice[]).map((choice) => (
              <label key={choice}>
                <input
                  type="radio"
                  name="access"
                  value={choice}
                  checked={form.access === choice}
                  onChange={() => set("access", choice)}
                />
                {ACCESS_LABELS[choice]}
              </label>
            ))}
          </div>
          {form.access === "ssh_tunnel" ? (
            <>
              <div className="fields-2" style={{ marginTop: 10 }}>
                <div className="field">
                  <label htmlFor="ssh-host">SSH host</label>
                  <input id="ssh-host" type="text" value={form.ssh.host} onChange={(e) => setSsh("host", e.target.value)} />
                </div>
                <div className="field">
                  <label htmlFor="ssh-port">SSH port</label>
                  <input id="ssh-port" type="number" value={form.ssh.port} onChange={(e) => setSsh("port", e.target.value)} />
                </div>
                <div className="field">
                  <label htmlFor="ssh-user">SSH user</label>
                  <input id="ssh-user" type="text" value={form.ssh.user} onChange={(e) => setSsh("user", e.target.value)} />
                </div>
                <div className="field">
                  <label htmlFor="ssh-key">SSH key path</label>
                  <input
                    id="ssh-key"
                    type="text"
                    placeholder="/home/operator/.ssh/id_ed25519"
                    value={form.ssh.keyPath}
                    onChange={(e) => setSsh("keyPath", e.target.value)}
                  />
                  <span className="hint">
                    A private key file on the machine running the API; its host must already be in that
                    machine's known_hosts. Key material is never uploaded.
                  </span>
                </div>
              </div>
              <div className="field">
                <label htmlFor="tunnel-auth">API credential behind the tunnel</label>
                <select
                  id="tunnel-auth"
                  value={form.tunnelAuth}
                  onChange={(e) => set("tunnelAuth", e.target.value as TunnelAuth)}
                >
                  <option value="none">none</option>
                  <option value="basic">user and password</option>
                  <option value="api_key">API key</option>
                </select>
              </div>
            </>
          ) : null}
          {auth === "basic" ? (
            <div className="fields-2">
              <div className="field">
                <label htmlFor="src-user">User</label>
                <input id="src-user" type="text" value={form.user} onChange={(e) => set("user", e.target.value)} autoComplete="off" />
              </div>
              <div className="field">
                <label htmlFor="src-password">Password</label>
                <input
                  id="src-password"
                  type="password"
                  value={form.password}
                  onChange={(e) => set("password", e.target.value)}
                  placeholder={editing ? "unchanged unless typed" : ""}
                  autoComplete="new-password"
                />
              </div>
            </div>
          ) : null}
          {auth === "api_key" ? (
            <div className="field">
              <label htmlFor="src-apikey">API key</label>
              <input
                id="src-apikey"
                type="password"
                value={form.apiKey}
                onChange={(e) => set("apiKey", e.target.value)}
                placeholder={editing ? "unchanged unless typed" : ""}
                autoComplete="new-password"
              />
              <span className="hint">Sent as a bearer token. Stored on the server, never shown again.</span>
            </div>
          ) : null}
        </fieldset>

        <fieldset>
          <legend>Run defaults</legend>
          <div className="fields-2">
            <div className="field">
              <label htmlFor="def-days">Days to read</label>
              <input id="def-days" type="number" min="0" step="any" value={form.defaults.days} onChange={(e) => setDef("days", e.target.value)} />
              <span className="hint">The most recent N days the source holds, resolved when a run starts.</span>
            </div>
            <div className="field">
              <label htmlFor="def-network">Network</label>
              <input id="def-network" type="text" value={form.defaults.network} onChange={(e) => setDef("network", e.target.value)} />
              <span className="hint">Which network this capture is of, as the operator names it.</span>
            </div>
            <div className="field">
              <label htmlFor="def-vantage">Vantage</label>
              <input id="def-vantage" type="text" value={form.defaults.vantage} onChange={(e) => setDef("vantage", e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="def-window">Window seconds</label>
              <input id="def-window" type="number" value={form.defaults.window} onChange={(e) => setDef("window", e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="def-sensors">Sensors</label>
              <input id="def-sensors" type="text" placeholder="comma-separated" value={form.defaults.sensors} onChange={(e) => setDef("sensors", e.target.value)} />
            </div>
          </div>
        </fieldset>

        {problems.length ? (
          <div className="error" role="alert">
            {problems.join("\n")}
          </div>
        ) : null}
        <ErrorLine error={serverError} prefix="the server refused the source" />
        <div className="row">
          <button className="primary" type="submit" disabled={saving || usable.length === 0}>
            {editing ? "Save changes" : "Save source"}
          </button>
          <Link to={editing && id ? `/discovery/sources/${encodeURIComponent(id)}` : "/discovery"}>Cancel</Link>
        </div>
      </form>
    </>
  );
}
