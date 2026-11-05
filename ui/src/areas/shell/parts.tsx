import { ApiError } from "../../api/client";
import { signinHref, useWhoami } from "../../api/whoami";

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
