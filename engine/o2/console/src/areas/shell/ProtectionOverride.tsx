/**
 * A PROTECTED twin (the server's protected-deploy list): every write against
 * it is refused (423) unless the request carries `override_protection=<its te
 * deploy id>`. The Console disables such writes by default and says why; this
 * is the explicit, separately confirmed step that lifts it for ONE change --
 * the operator types the deploy id, and only then is the override sent.
 */
import { useState } from "react";

/** The typed confirmation must equal the deploy id exactly (surrounding
 *  whitespace forgiven -- a paste often carries it). Shared by destroy. */
export function typedMatches(typed: string, deployId: string): boolean {
  return typed.trim() === deployId;
}

export function ProtectedBadge({ title }: { title?: string }): JSX.Element {
  return (
    <span
      className="tag protected"
      data-testid="protected-badge"
      title={title ?? "Protected: the server refuses changes to this twin unless you explicitly override protection."}
    >
      Protected
    </span>
  );
}

export function ProtectionOverride({
  deployId,
  value,
  onChange,
  what,
  startOpen = false,
}: {
  deployId: string;
  /** Open straight at the typing step (the operator already asked, by
   *  activating a locked control). */
  startOpen?: boolean;
  /** The override in force for this change, or `null`. */
  value: string | null;
  onChange: (override: string | null) => void;
  /** What the override is for, in words ("this persona change"). */
  what: string;
}): JSX.Element {
  const [open, setOpen] = useState(startOpen);
  const [typed, setTyped] = useState("");
  if (value !== null) {
    return (
      <p className="notice" data-testid="protection-overridden">
        Protection is overridden for {what}.{" "}
        <button type="button" className="link" onClick={() => onChange(null)}>
          Undo
        </button>
      </p>
    );
  }
  return (
    <div className="notice protection" data-testid="protection-gate">
      <p>
        <ProtectedBadge /> <span className="mono">{deployId}</span> is protected, so {what} is refused unless you
        override protection.
      </p>
      {!open ? (
        <button type="button" className="btn small" onClick={() => setOpen(true)} data-testid="protection-open">
          Override protection…
        </button>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (typedMatches(typed, deployId)) onChange(deployId);
          }}
        >
          <label className="field">
            <span>
              Type <span className="mono">{deployId}</span> to override protection for {what}
            </span>
            <input
              value={typed}
              autoComplete="off"
              spellCheck={false}
              onChange={(e) => setTyped(e.target.value)}
              autoFocus={startOpen}
              data-testid="protection-typed"
            />
          </label>
          <div className="actions">
            <button type="submit" className="btn danger" disabled={!typedMatches(typed, deployId)} data-testid="protection-confirm">
              Override protection
            </button>
            <button type="button" className="btn" onClick={() => setOpen(false)}>
              Cancel
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

export const PROTECTED_REASON = "Protected — writes are refused unless you override by name";

/** A small padlock, drawn (no icon font, no emoji); decorative. */
export function LockIcon(): JSX.Element {
  return (
    <svg className="lock-icon" width="12" height="12" viewBox="0 0 12 12" aria-hidden="true" focusable="false">
      <rect x="2" y="5.5" width="8" height="5.5" rx="1" fill="currentColor" />
      <path d="M3.8 5.5V4a2.2 2.2 0 0 1 4.4 0v1.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  );
}

/**
 * A WRITE control on a twin that may be protected. Unprotected (or already
 * overridden for this page): the ordinary button. Protected: a visibly LOCKED,
 * muted control with the reason beside it; activating it opens the override
 * step (type the deploy id) -- the only way forward.
 */
export function ProtectedAction(props: {
  protectedTwin: boolean;
  deployId: string;
  override: string | null;
  onOverride: (override: string | null) => void;
  /** What the write is, for the override prompt ("destroying it"). */
  what: string;
  label: string;
  className: string;
  onAct: () => void;
  testId: string;
}): JSX.Element {
  const [asking, setAsking] = useState(false);
  if (!props.protectedTwin || props.override !== null) {
    return (
      <button type="button" className={props.className} onClick={props.onAct} data-testid={props.testId}>
        {props.label}
      </button>
    );
  }
  return (
    <span className="locked-action">
      <button
        type="button"
        className="btn locked"
        aria-disabled="true"
        aria-describedby={`${props.testId}-reason`}
        onClick={() => setAsking(true)}
        data-testid={props.testId}
        data-locked="true"
      >
        <LockIcon /> {props.label}
      </button>{" "}
      <span id={`${props.testId}-reason`} className="lock-reason" data-testid={`${props.testId}-reason`}>
        {PROTECTED_REASON}
      </span>
      {asking && (
        <ProtectionOverride
          deployId={props.deployId}
          value={null}
          onChange={(v) => {
            setAsking(false);
            props.onOverride(v);
          }}
          what={props.what}
          startOpen
        />
      )}
    </span>
  );
}
