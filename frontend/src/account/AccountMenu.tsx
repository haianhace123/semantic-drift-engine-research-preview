import {
  useEffect,
  useRef,
  useState,
} from "react";

import type {
  CurrentUser,
} from "../api/auth";

import {
  ApiError,
} from "../api/client";

import {
  signOut,
} from "./api";


interface AccountMenuProps {
  user: CurrentUser;
  onOpenAccount: () => void;
}


function initials(
  name: string,
  email: string,
) {
  const source =
    name.trim() || email.trim();

  return source
    .split(/\s+/)
    .slice(0, 2)
    .map((part) =>
      part.charAt(0).toUpperCase(),
    )
    .join("");
}


export function AccountMenu({
  user,
  onOpenAccount,
}: AccountMenuProps) {
  const [open, setOpen] =
    useState(false);

  const [signingOut, setSigningOut] =
    useState(false);

  const [error, setError] =
    useState<string | null>(null);

  const rootRef =
    useRef<HTMLDivElement | null>(null);

  const triggerRef =
    useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!open) {
      return;
    }

    function handlePointerDown(
      event: PointerEvent,
    ) {
      if (
        rootRef.current
        && !rootRef.current.contains(
          event.target as Node,
        )
      ) {
        setOpen(false);
      }
    }

    function handleKeyDown(
      event: KeyboardEvent,
    ) {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    }

    document.addEventListener(
      "pointerdown",
      handlePointerDown,
    );

    document.addEventListener(
      "keydown",
      handleKeyDown,
    );

    return () => {
      document.removeEventListener(
        "pointerdown",
        handlePointerDown,
      );

      document.removeEventListener(
        "keydown",
        handleKeyDown,
      );
    };
  }, [open]);

  async function handleSignOut() {
    if (signingOut) {
      return;
    }

    setSigningOut(true);
    setError(null);

    try {
      await signOut();

      window.location.replace(
        "/login.html",
      );
    } catch (caught) {
      setSigningOut(false);

      setError(
        caught instanceof ApiError
          ? caught.message
          : "Sign out could not be completed.",
      );
    }
  }

  return (
    <div
      className="account-menu"
      ref={rootRef}
    >
      <button
        ref={triggerRef}
        className="account-trigger"
        type="button"
        aria-label={`Account menu for ${user.full_name || user.email}`}
        aria-expanded={open}
        aria-controls={open ? "sde-account-popover" : undefined}
        onClick={() => {
          setOpen((value) => !value);
          setError(null);
        }}
      >
        <span
          className="account-avatar"
          aria-hidden="true"
        >
          {initials(
            user.full_name,
            user.email,
          )}
        </span>

        <span className="account-trigger-copy">
          <strong>
            {user.full_name || user.email}
          </strong>
          <span>{user.role}</span>
        </span>

        <svg
          className="account-chevron"
          viewBox="0 0 16 16"
          aria-hidden="true"
        >
          <path
            d="M4 6.25 8 10l4-3.75"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.25"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>

      {open ? (
        <div
          id="sde-account-popover"
          className="account-popover"
        >
          <div className="account-popover-identity">
            <strong>
              {user.full_name || "Account"}
            </strong>
            <span>{user.email}</span>
          </div>

          <div className="account-popover-actions">
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                onOpenAccount();
              }}
            >
              <svg
                viewBox="0 0 20 20"
                aria-hidden="true"
              >
                <circle
                  cx="10"
                  cy="7"
                  r="3"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.25"
                />
                <path
                  d="M4.5 16c.5-3 2.35-4.5 5.5-4.5s5 1.5 5.5 4.5"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.25"
                  strokeLinecap="round"
                />
              </svg>

              <span>
                <strong>
                  Account &amp; security
                </strong>
                <small>
                  Profile, sessions and access
                </small>
              </span>
            </button>
          </div>

          {error ? (
            <p
              className="account-menu-error"
              role="alert"
            >
              {error}
            </p>
          ) : null}

          <button
            className="account-signout"
            type="button"
            disabled={signingOut}
            onClick={handleSignOut}
          >
            <svg
              viewBox="0 0 20 20"
              aria-hidden="true"
            >
              <path
                d="M8 4H4.75v12H8M11.5 6.5 15 10l-3.5 3.5M7.5 10H15"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.25"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>

            {signingOut
              ? "Signing out..."
              : "Sign out"}
          </button>
        </div>
      ) : null}
    </div>
  );
}
