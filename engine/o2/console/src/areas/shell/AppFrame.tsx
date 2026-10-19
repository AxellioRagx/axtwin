/**
 * The app frame: the top navigation (Range · Catalog · Design · Telemetry),
 * the cluster indicator, the write-token control, and the area the URL names.
 *
 * Old `/imprints/...` links are redirected (with `replace`, so Back does not
 * bounce) to the same place under `/design`.
 */
import { useMemo } from "react";
import { Link, Navigate, useLocation } from "react-router";

import { useTargets } from "../../api/runtimeHooks";
import { areaOf, parseAppPath, type AppArea } from "../../model/appRoutes";
import { ConsoleShell } from "../../App";
import { CatalogPage } from "../catalog/CatalogPage";
import { RangePage } from "../range/RangePage";
import { TelemetryPage } from "../telemetry/TelemetryPage";
import { TwinPage } from "../twin/TwinPage";
import { useWriteAccess } from "./writeAccess";

const AREAS: ReadonlyArray<{ area: AppArea; label: string; to: string; hint: string }> = [
  { area: "range", label: "Range", to: "/range", hint: "What is deployed, on which cluster" },
  { area: "catalog", label: "Catalog", to: "/catalog", hint: "Twins and scenarios you can deploy" },
  { area: "design", label: "Design", to: "/design", hint: "Edit a twin's design (assertions canvas)" },
  { area: "telemetry", label: "Telemetry", to: "/telemetry", hint: "Capture and alert dashboards (Grafana)" },
];

export function AppFrame(): JSX.Element {
  const location = useLocation();
  const route = useMemo(() => parseAppPath(location.pathname, location.search), [location.pathname, location.search]);

  if (route.area === "redirect") return <Navigate to={route.to} replace />;

  // The Twin page lives under the Range in the navigation.
  const area = areaOf(route);
  const current: AppArea | null = area === "twin" ? "range" : area;

  return (
    <div className={`app${route.area === "design" ? " is-design" : ""}`}>
      <header className="app-head">
        <Link to="/range" className="app-brand" aria-label="axRange Console home">
          axRange <span>Console</span>
        </Link>
        <nav aria-label="Areas" className="app-nav">
          {AREAS.map((a) => (
            <Link
              key={a.area}
              to={a.to}
              title={a.hint}
              className={`app-nav-link${current === a.area ? " is-current" : ""}`}
              aria-current={current === a.area ? "page" : undefined}
              data-testid={`nav-${a.area}`}
            >
              {a.label}
            </Link>
          ))}
        </nav>
        <span className="app-head-spacer" />
        <ClusterIndicator />
        <TokenControl />
      </header>
      <main className="app-main">
        {route.area === "range" && <RangePage />}
        {route.area === "twin" && (
          <TwinPage key={route.deployId} deployId={route.deployId} host={route.host} personas={route.personas} />
        )}
        {route.area === "catalog" && <CatalogPage kind={route.kind} id={route.id} />}
        {route.area === "telemetry" && <TelemetryPage />}
        {route.area === "design" && (
          <div className="design-area">
            <ConsoleShell />
          </div>
        )}
        {route.area === "unknown" && (
          <section className="page">
            <h1>No such page</h1>
            <p>
              <code>{location.pathname}</code> is not a page of this Console. Start from the{" "}
              <Link to="/range">Range</Link>.
            </p>
          </section>
        )}
      </main>
    </div>
  );
}

/** Which cluster(s) this Console reads, from `GET /api/targets`. Recorded
 *  configuration, not a live read -- reachability is on the Range. */
function ClusterIndicator(): JSX.Element {
  const targets = useTargets();
  let text: string;
  let title: string;
  if (targets.loading) {
    text = "cluster: …";
    title = "Reading the configured cluster targets";
  } else if (targets.error || !targets.data) {
    text = "cluster: unknown";
    title = targets.error ? `GET /api/targets failed: ${targets.error.message}` : "No answer";
  } else {
    const list = targets.data.targets;
    const names = list.map((t) => t.name ?? t.node);
    const def = list.find((t) => t.default) ?? list[0];
    text =
      list.length === 0
        ? "no cluster configured"
        : list.length === 1
          ? `cluster: ${names[0]}`
          : `${list.length} clusters (default ${def?.name ?? def?.node ?? "?"})`;
    title = list.map((t) => `${t.name ?? t.node}${t.api_url ? ` — ${t.api_url}` : ""}${t.error ? ` (error: ${t.error})` : ""}`).join("\n");
  }
  return (
    <span className="cluster-indicator" title={title} data-testid="cluster-indicator">
      {text}
    </span>
  );
}

function TokenControl(): JSX.Element {
  const access = useWriteAccess();
  return access.hasToken ? (
    <span className="token-control">
      <span className="token-state is-set">write token set</span>
      <button type="button" className="link" onClick={access.forgetToken} data-testid="token-forget">
        forget
      </button>
    </span>
  ) : (
    <button
      type="button"
      className="btn small token-control"
      onClick={access.openTokenDialog}
      data-testid="token-open"
      title="Only needed when this server requires a token for changes; you will be asked when a change needs it."
    >
      Write token: not set
    </button>
  );
}
