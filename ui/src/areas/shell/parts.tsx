import type { ReactNode } from "react";
import { Link, useNavigate } from "react-router";

import { ApiError } from "../../api/client";
import { signinHref, useWhoami } from "../../api/whoami";
import { formatInstant, formatInstantUtc, instantOf } from "../../model/time";

/** A refused write, in words the operator can act on (ui/Spec.md C1.6): a
 *  `401` is "sign in required", a `403` "read-only for this account". Neither
 *  is a source's own credential failure, which keeps the server's wording. */
export function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.kind === "unauthorized") return `sign in required: ${error.message}`;
    if (error.kind === "forbidden") return `read-only for this account: ${error.message}`;
    return error.message;
  }
  if (error instanceof Error) return error.message;
  return String(error);
}

/** The server's refusal in its own words (ui/Spec.md C1.3), never "something went wrong".
 *  A `401` links the deployment's sign-in URL when it serves one. */
export function ErrorLine({ error, prefix }: { error: unknown; prefix?: string }) {
  const whoami = useWhoami();
  if (!error) return null;
  const signin = error instanceof ApiError && error.kind === "unauthorized" ? signinHref(whoami.signin_url) : null;
  return (
    <div className="error" role="alert">
      {prefix ? `${prefix}: ` : ""}
      {errorText(error)}
      {signin ? (
        <>
          {" "}
          <a href={signin}>Sign in</a>
        </>
      ) : null}
    </div>
  );
}

export function StatusBadge({ status }: { status: string }) {
  return <span className={`badge ${status}`}>{status}</span>;
}

/** The ONE date style of the console (`model/time.ts`): `2026-11-07 14:02 CST`, local time with
 *  the zone, on one line; `When` puts the exact UTC instant in the tooltip. Accepts an ISO string
 *  or epoch seconds. */
export function formatWhen(value: string | number | null | undefined): string {
  return formatInstant(value);
}

export function When({ value }: { value: string | number | null | undefined }) {
  const d = instantOf(value);
  if (!d) return <span className="muted">—</span>;
  return (
    <time className="when" dateTime={d.toISOString()} title={formatInstantUtc(value)}>
      {formatInstant(value)}
    </time>
  );
}

/** The ONE name+id presentation: the name in normal weight, the id as small muted mono
 *  beneath it (omitted when it is the same string). `to` makes the name the way in. */
export function EntityName({ name, id, to }: { name?: string | null; id: string; to?: string }) {
  const shown = name || id;
  return (
    <span className="entity">
      {to ? (
        <Link className="navlink" to={to}>
          {shown}
        </Link>
      ) : (
        <span>{shown}</span>
      )}
      {shown !== id ? <span className="entity-id mono muted">{id}</span> : null}
    </span>
  );
}

/** A table row that is itself the navigation: a click anywhere on it (outside a link or
 *  button) goes to `to`; the link inside keeps the keyboard and screen-reader path. */
export function LinkRow({ to, children, ...rest }: { to: string; children: ReactNode; "data-testid"?: string }) {
  const navigate = useNavigate();
  return (
    <tr
      className="clickable"
      onClick={(e) => {
        if ((e.target as HTMLElement).closest("a,button,input,select,textarea")) return;
        navigate(to);
      }}
      {...rest}
    >
      {children}
    </tr>
  );
}
