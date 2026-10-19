/**
 * The ONE place the write token is entered (ruling §2; README "Configuration").
 *
 * The token still lives only in `api/client.ts`'s module variable -- never in
 * storage, a cookie or the bundle -- and this provider is just the UI over it:
 *
 *   - a labelled header control ("Write token: not set") that opens the
 *     dialog on purpose, and
 *   - `runWrite(what, fn)`, which runs a write and, when the server answers
 *     401, asks for the token WITH the reason ("Deploying corp-twin needs the
 *     write token") and retries once the operator supplies it. A server with
 *     no token configured never prompts at all.
 *
 * Cancelling the prompt re-throws the original 401, so the calling flow shows
 * its own "not authorized" result rather than hanging.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { ConsoleApiError, getWriteToken, onWriteTokenChange, setWriteToken } from "../../api/client";

export interface WriteAccess {
  hasToken: boolean;
  openTokenDialog: () => void;
  forgetToken: () => void;
  runWrite: <T>(what: string, fn: () => Promise<T>) => Promise<T>;
}

const WriteAccessContext = createContext<WriteAccess | null>(null);

export function useWriteAccess(): WriteAccess {
  const value = useContext(WriteAccessContext);
  if (value) return value;
  // Outside the provider (a unit test of one view): writes run as-is.
  return FALLBACK;
}

const FALLBACK: WriteAccess = {
  hasToken: false,
  openTokenDialog: () => undefined,
  forgetToken: () => undefined,
  runWrite: (_what, fn) => fn(),
};

interface Prompt {
  reason: string | null;
  resolve: () => void;
  reject: () => void;
}

export function WriteAccessProvider({ children }: { children: ReactNode }): JSX.Element {
  const [hasToken, setHasToken] = useState(() => getWriteToken() !== null);
  const [prompt, setPrompt] = useState<Prompt | null>(null);
  useEffect(() => onWriteTokenChange(() => setHasToken(getWriteToken() !== null)), []);

  const ask = useCallback(
    (reason: string | null) =>
      new Promise<void>((resolve, reject) => {
        setPrompt({ reason, resolve, reject });
      }),
    [],
  );

  const openTokenDialog = useCallback(() => {
    void ask(null).catch(() => undefined);
  }, [ask]);

  const forgetToken = useCallback(() => {
    setWriteToken(null);
  }, []);

  const runWrite = useCallback(
    async <T,>(what: string, fn: () => Promise<T>): Promise<T> => {
      for (;;) {
        try {
          return await fn();
        } catch (cause) {
          if (!(cause instanceof ConsoleApiError) || cause.kind !== "unauthorized") throw cause;
          try {
            await ask(
              getWriteToken()
                ? `${what} was refused: the server did not accept this write token.`
                : `${what} needs this server's write token.`,
            );
          } catch {
            throw cause;
          }
        }
      }
    },
    [ask],
  );

  const value = useMemo<WriteAccess>(
    () => ({ hasToken, openTokenDialog, forgetToken, runWrite }),
    [hasToken, openTokenDialog, forgetToken, runWrite],
  );

  return (
    <WriteAccessContext.Provider value={value}>
      {children}
      {prompt && (
        <TokenDialog
          reason={prompt.reason}
          onSubmit={(token) => {
            setWriteToken(token);
            setPrompt(null);
            prompt.resolve();
          }}
          onCancel={() => {
            setPrompt(null);
            prompt.reject();
          }}
        />
      )}
    </WriteAccessContext.Provider>
  );
}

function TokenDialog({
  reason,
  onSubmit,
  onCancel,
}: {
  reason: string | null;
  onSubmit: (token: string) => void;
  onCancel: () => void;
}): JSX.Element {
  const [value, setValue] = useState("");
  const inputRef = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    inputRef.current?.focus();
  }, []);
  return (
    <div className="modal-backdrop">
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="token-title"
        data-testid="token-dialog"
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.stopPropagation();
            onCancel();
          }
        }}
      >
        <h2 id="token-title">Write token</h2>
        {reason && <p className="notice">{reason}</p>}
        <p className="meta">
          Kept in this tab's memory only. It is never saved, and a reload forgets it.
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (value.trim()) onSubmit(value);
          }}
        >
          <label className="field">
            <span>Token</span>
            <input
              ref={inputRef}
              type="password"
              autoComplete="off"
              value={value}
              data-testid="token-input"
              onChange={(e) => setValue(e.target.value)}
            />
          </label>
          <div className="actions">
            <button type="submit" className="btn primary" disabled={!value.trim()} data-testid="token-submit">
              Use this token
            </button>
            <button type="button" className="btn" onClick={onCancel} data-testid="token-cancel">
              Cancel
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
