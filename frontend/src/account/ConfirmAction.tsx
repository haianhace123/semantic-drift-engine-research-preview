import {
  useEffect,
  useRef,
  useState,
} from "react";

interface ConfirmActionProps {
  title: string;
  description: string;
  confirmLabel: string;
  confirmationText?: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmAction({
  title,
  description,
  confirmLabel,
  confirmationText,
  busy = false,
  onConfirm,
  onCancel,
}: ConfirmActionProps) {
  const dialogRef = useRef<HTMLDialogElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const [verification, setVerification] =
    useState("");

  const verified =
    confirmationText === undefined
    || verification === confirmationText;

  useEffect(() => {
    const dialog = dialogRef.current;

    if (!dialog) {
      return;
    }

    dialog.showModal();

    if (confirmationText) {
      inputRef.current?.focus();
    }

    return () => {
      if (dialog.open) {
        dialog.close();
      }
    };
  }, [confirmationText]);

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (busy) {
      return;
    }

    if (!verified) {
      inputRef.current?.focus();
      return;
    }

    onConfirm();
  }

  return (
    <dialog
      ref={dialogRef}
      className="confirm-action"
      onCancel={(event) => {
        event.preventDefault();

        if (!busy) {
          onCancel();
        }
      }}
      onClick={(event) => {
        if (
          !busy
          && event.target === event.currentTarget
        ) {
          onCancel();
        }
      }}
    >
      <form
        method="dialog"
        className="confirm-action-panel"
        onSubmit={submit}
      >
        <span className="surface-kicker">
          Security confirmation
        </span>

        <h3>{title}</h3>
        <p>{description}</p>

        {confirmationText ? (
          <label className="confirm-action-verification">
            <span>
              Type <code>{confirmationText}</code> to confirm
            </span>
            <input
              ref={inputRef}
              autoComplete="off"
              spellCheck={false}
              value={verification}
              onChange={(event) =>
                setVerification(event.target.value)
              }
              aria-invalid={!verified}
            />
          </label>
        ) : null}

        <div className="confirm-action-actions">
          <button
            type="button"
            disabled={busy}
            onClick={onCancel}
          >
            Cancel
          </button>

          <button
            className="is-danger"
            type="submit"
            disabled={busy || !verified}
          >
            {busy ? "Working..." : confirmLabel}
          </button>
        </div>
      </form>
    </dialog>
  );
}
