import { ApiError } from "../../api/client";

export function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.message;
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
