import { ApiError, getWriteToken } from "../../api/client";

export function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.kind === "unauthorized") {
      // The server says "missing or invalid API token"; the operator needs to know WHICH
      // token -- this console's write token, not the source's own credential.
      const why = getWriteToken()
        ? "the write token in the header was refused"
        : "no write token is set in the header (top right)";
      return `${error.message}. ${why}; it is the axrange API's own token (AXRANGE_API_TOKEN), not the source's credential`;
    }
    return error.message;
  }
  if (error instanceof Error) return error.message;
  return String(error);
}

/** The server's refusal in its own words (ui/Spec.md C1.3), never "something went wrong". */
export function ErrorLine({ error, prefix }: { error: unknown; prefix?: string }) {
  if (!error) return null;
  return (
    <div className="error" role="alert">
      {prefix ? `${prefix}: ` : ""}
      {errorText(error)}
    </div>
  );
}

export function StatusBadge({ status }: { status: string }) {
  return <span className={`badge ${status}`}>{status}</span>;
}
