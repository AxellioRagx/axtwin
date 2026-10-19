/**
 * Small HTML parts the run-time screens share: an error in plain words, a
 * recorded/live tag, a status badge. One spelling each (rule 3: recorded vs
 * live is always labelled -- the same way everywhere).
 */
import type { ConsoleApiError } from "../../api/client";
import { ago, statusTone, statusWord } from "../../model/format";

/** FastAPI's own 404 for a route it does not have is `{"detail": "Not
 *  Found"}`, while an axRange 404 names the missing thing -- so a bare "Not
 *  Found" means the SERVER predates the endpoint, not that the deploy is gone. */
export function isMissingEndpoint(error: ConsoleApiError): boolean {
  return error.kind === "not-found" && error.message === "Not Found";
}

/** What a failed request means, said for the operator rather than as HTTP. */
export function errorWords(error: ConsoleApiError, notFound?: string): string {
  switch (error.kind) {
    case "network":
      return `The Console could not reach its server (${error.message}).`;
    case "not-found":
      return notFound ?? `Not found: ${error.message}`;
    case "unauthorized":
      return "The server refused the write token (or needs one). Set it from “Write token” at the top right.";
    case "locked":
      return "This twin is protected, so the server refused the change. Override protection explicitly (you will be asked to type the deploy id) or leave it alone.";
    case "conflict":
      return `Something else is changing this right now: ${error.message}`;
    case "unavailable":
      return `The server cannot do this in its current configuration: ${error.message}`;
    case "unprocessable":
    case "bad-request":
      return `The server refused the request: ${error.message}`;
    default:
      return error.message;
  }
}

export function ErrorNotice({
  error,
  what,
  notFound,
}: {
  error: ConsoleApiError;
  what: string;
  notFound?: string;
}): JSX.Element {
  return (
    <p className="notice is-error" role="alert">
      <strong>{what} failed.</strong> {errorWords(error, notFound)}
    </p>
  );
}

export function RecordedTag({ title }: { title?: string }): JSX.Element {
  return (
    <span className="src-tag is-recorded" title={title ?? "From the state store: what axRange recorded when it deployed or applied. Not a read of the cluster."}>
      recorded
    </span>
  );
}

export function LiveTag({ checkedAt, now }: { checkedAt: number | null | undefined; now: number }): JSX.Element {
  return (
    <span className="src-tag is-live" title="Read from the cluster when you asked.">
      live · {ago(checkedAt, now)}
    </span>
  );
}

export function StatusBadge({ status, title }: { status: string | null | undefined; title?: string }): JSX.Element {
  return (
    <span className={`status tone-${statusTone(status)}`} title={title}>
      {statusWord(status)}
    </span>
  );
}
