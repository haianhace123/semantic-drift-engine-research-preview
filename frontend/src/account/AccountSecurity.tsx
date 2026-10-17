import {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";

import type {
  CurrentUser,
} from "../api/auth";

import {
  ApiError,
} from "../api/client";

import {
  ConfirmAction,
} from "./ConfirmAction";


import {
  changePassword,
  createApiKey,
  deleteAccount,
  exportAccount,
  getApiKeys,
  getSessions,
  revokeAllSessions,
  revokeApiKey,
  revokeSession,
  updateProfile,
  type ApiKeyRecord,
  type AuthSession,
  type CreatedApiKey,
} from "./api";


type PendingAction =
  | { type: "revoke-session"; session: AuthSession }
  | { type: "revoke-key"; key: ApiKeyRecord }
  | { type: "revoke-all" }
  | { type: "delete-account" };


interface AccountSecurityProps {
  user: CurrentUser;
  onClose: () => void;
  onUserChanged: (user: CurrentUser) => void;
}


function messageFrom(error: unknown) {
  return error instanceof ApiError
    ? error.message
    : "The operation could not be completed.";
}


function formatTime(value: string | null) {
  if (!value) {
    return "--";
  }

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}


function sessionClient(userAgent: string | null) {
  if (!userAgent) {
    return "Unknown client";
  }

  if (/Edg\//i.test(userAgent)) {
    return "Microsoft Edge";
  }

  if (/Chrome\//i.test(userAgent)) {
    return "Google Chrome";
  }

  if (/Firefox\//i.test(userAgent)) {
    return "Firefox";
  }

  if (/Safari\//i.test(userAgent)) {
    return "Safari";
  }

  return "Browser session";
}


function downloadJson(
  filename: string,
  value: unknown,
) {
  const blob = new Blob(
    [JSON.stringify(value, null, 2)],
    { type: "application/json" },
  );

  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");

  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();

  // A browser may complete the download after the click handler returns.
  // Revoking synchronously can invalidate the download in some browsers.
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
}


export function AccountSecurity({
  user,
  onClose,
  onUserChanged,
}: AccountSecurityProps) {
  const [sessions, setSessions] =
    useState<AuthSession[]>([]);

  const [apiKeys, setApiKeys] =
    useState<ApiKeyRecord[]>([]);

  const [loading, setLoading] =
    useState(true);

  const [error, setError] =
    useState<string | null>(null);

  const [notice, setNotice] =
    useState<string | null>(null);

  const [fullName, setFullName] =
    useState(user.full_name);

  const [savingProfile, setSavingProfile] =
    useState(false);

  const [currentPassword, setCurrentPassword] =
    useState("");

  const [newPassword, setNewPassword] =
    useState("");

  const [confirmPassword, setConfirmPassword] =
    useState("");

  const [changingPassword, setChangingPassword] =
    useState(false);

  const [keyName, setKeyName] =
    useState("");

  const [keyRead, setKeyRead] =
    useState(true);

  const [keyWrite, setKeyWrite] =
    useState(false);

  const [creatingKey, setCreatingKey] =
    useState(false);

  const [createdKey, setCreatedKey] =
    useState<CreatedApiKey | null>(null);

  const [keyCopied, setKeyCopied] =
    useState(false);

  const [busyId, setBusyId] =
    useState<string | null>(null);

  const [pendingAction, setPendingAction] =
    useState<PendingAction | null>(null);

  const currentSession = useMemo(
    () => sessions.find(
      (session) => session.is_current,
    ) ?? null,
    [sessions],
  );

  // Older APIs may omit this field; keep the prior form only until their
  // authenticated identity response is upgraded.
  const passwordLoginEnabled = user.password_login_enabled !== false;

  const load = useCallback(
    async (signal?: AbortSignal) => {
      setLoading(true);
      setError(null);

      try {
        const sessionResponse = await getSessions(signal);
        setSessions(sessionResponse.sessions);

        if (user.role === "admin") {
          const keyResponse = await getApiKeys(signal);
          setApiKeys(keyResponse.data);
        } else {
          setApiKeys([]);
        }
      } catch (caught) {
        if (
          caught instanceof DOMException
          && caught.name === "AbortError"
        ) {
          return;
        }

        setError(messageFrom(caught));
      } finally {
        if (!signal?.aborted) {
          setLoading(false);
        }
      }
    },
    [user.role],
  );

  useEffect(() => {
    const controller = new AbortController();

    void load(controller.signal);

    return () => controller.abort();
  }, [load]);

  async function saveProfile() {
    const name = fullName.trim();

    if (savingProfile || name === user.full_name) {
      return;
    }

    setSavingProfile(true);
    setError(null);
    setNotice(null);

    try {
      const updated = await updateProfile(name);
      onUserChanged(updated);
      setFullName(updated.full_name);
      setNotice("Profile updated.");
    } catch (caught) {
      setError(messageFrom(caught));
    } finally {
      setSavingProfile(false);
    }
  }

  async function handlePasswordChange() {
    if (
      changingPassword
      || !currentPassword
      || !newPassword
      || !confirmPassword
    ) {
      return;
    }

    setChangingPassword(true);
    setError(null);
    setNotice(null);

    try {
      const result = await changePassword(
        currentPassword,
        newPassword,
        confirmPassword,
      );

      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");

      if (result.reauthenticate) {
        window.location.replace("/login.html");
        return;
      }

      setNotice(result.message);
    } catch (caught) {
      setError(messageFrom(caught));
    } finally {
      setChangingPassword(false);
    }
  }

  async function handleRevokeSession(
    session: AuthSession,
  ) {
    if (busyId) {
      return false;
    }

    setBusyId(session.id);
    setError(null);
    setNotice(null);

    try {
      const result = await revokeSession(session.id);

      if (result.current_session_revoked) {
        window.location.replace("/login.html");
        return;
      }

      setSessions((current) =>
        current.filter(
          (item) => item.id !== session.id,
        ),
      );

      setNotice("Session revoked.");
      return true;
    } catch (caught) {
      setError(messageFrom(caught));
      return false;
    } finally {
      setBusyId(null);
    }
  }

  async function handleRevokeAll() {
    if (busyId) {
      return;
    }

    setBusyId("all-sessions");
    setError(null);

    try {
      await revokeAllSessions();
      window.location.replace("/login.html");
    } catch (caught) {
      setError(messageFrom(caught));
      setBusyId(null);
    }
  }

  function selectCreatedKeyText(): boolean {
    const element = document.getElementById("sde-created-api-key");
    const selection = window.getSelection();

    if (!element || !selection) {
      return false;
    }

    const range = document.createRange();
    range.selectNodeContents(element);
    selection.removeAllRanges();
    selection.addRange(range);
    return true;
  }

  async function handleCopyCreatedKey() {
    if (!createdKey) {
      return;
    }

    setError(null);
    setNotice(null);
    setKeyCopied(false);

    try {
      if (!navigator.clipboard?.writeText) {
        throw new Error("Clipboard API is unavailable.");
      }

      await navigator.clipboard.writeText(createdKey.key);
      setKeyCopied(true);
      setNotice("Credential copied to clipboard.");
    } catch {
      const selected = selectCreatedKeyText();
      setError(
        selected
          ? "Clipboard access was blocked. The key is selected: press Ctrl+C to copy it."
          : "Clipboard access was blocked. Select the key text and press Ctrl+C.",
      );
    }
  }


  async function handleCreateKey() {
    const name = keyName.trim();

    if (creatingKey || !name) {
      return;
    }

    setCreatingKey(true);
    setError(null);
    setNotice(null);
    setCreatedKey(null);

    try {
      const result = await createApiKey({
        name,
        permissions: {
          read: keyRead,
          write: keyWrite,
        },
      });

      setCreatedKey(result);
      setKeyCopied(false);
      setKeyName("");

      const response = await getApiKeys();
      setApiKeys(response.data);
    } catch (caught) {
      setError(messageFrom(caught));
    } finally {
      setCreatingKey(false);
    }
  }

  async function handleRevokeKey(
    key: ApiKeyRecord,
  ) {
    if (busyId) {
      return false;
    }

    setBusyId(key.id);
    setError(null);

    try {
      await revokeApiKey(key.id);

      setApiKeys((current) =>
        current.filter(
          (item) => item.id !== key.id,
        ),
      );

      setNotice("API key revoked.");
      return true;
    } catch (caught) {
      setError(messageFrom(caught));
      return false;
    } finally {
      setBusyId(null);
    }
  }

  async function handleExport() {
    setError(null);

    try {
      const data = await exportAccount();

      downloadJson(
        "semantic-drift-account-export.json",
        data,
      );

      setNotice("Account export prepared.");
    } catch (caught) {
      setError(messageFrom(caught));
    }
  }

  async function handleDeleteAccount() {
    setBusyId("delete-account");
    setError(null);

    try {
      await deleteAccount();
      window.location.replace("/login.html");
    } catch (caught) {
      // A 409 means the server preserved organization ownership. The
      // confirmation dialog must close so the refusal stays visible.
      setPendingAction(null);
      setError(
        caught instanceof ApiError && caught.status === 409
          ? (
            "Deactivation was blocked to protect organization ownership. "
            + "Your account remains active. Another active administrator "
            + "or a reviewed organization closure is required first."
          )
          : messageFrom(caught),
      );
      setBusyId(null);
    }
  }

  return (
    <section
      className="account-security"
      aria-labelledby="accountSecurityTitle"
    >
      <header className="account-security-header">
        <div>
          <span className="surface-kicker">
            Identity control
          </span>

          <h2 id="accountSecurityTitle" tabIndex={-1}>
            Account &amp; security
          </h2>

          <p>
            Manage identity, active sessions and
            programmatic access to this organization.
          </p>
        </div>

        <button
          className="account-security-close"
          type="button"
          onClick={onClose}
          aria-label="Close account and security"
        >
          <svg
            viewBox="0 0 20 20"
            aria-hidden="true"
          >
            <path
              d="M5 5l10 10M15 5 5 15"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.25"
              strokeLinecap="round"
            />
          </svg>
        </button>
      </header>

      {error ? (
        <div
          className="account-security-message is-error"
          role="alert"
        >
          {error}
        </div>
      ) : null}

      {notice ? (
        <div
          className="account-security-message"
          role="status"
        >
          {notice}
        </div>
      ) : null}

      <div className="account-security-layout">
        <nav
          className="account-security-index"
          aria-label="Account sections"
        >
          <a href="#account-identity">Identity</a>
          <a href="#account-sessions">Sessions</a>
          <a href="#account-password">Password</a>
          {user.role === "admin" ? (
            <a href="#account-api">Organization API access</a>
          ) : null}
          <a href="#account-data">Account data</a>
        </nav>

        <div className="account-security-content">
          <section id="account-identity">
            <header>
              <span>01</span>
              <div>
                <h3>Identity</h3>
                <p>
                  Account identity and organization context.
                </p>
              </div>
            </header>

            <div className="account-identity-grid">
              <label>
                <span>Full name</span>
                <input
                  value={fullName}
                  maxLength={255}
                  onChange={(event) =>
                    setFullName(event.target.value)
                  }
                />
              </label>

              <div className="account-readout">
                <span>Email</span>
                <strong>{user.email}</strong>
              </div>

              <div className="account-readout">
                <span>Role</span>
                <strong>{user.role}</strong>
              </div>

              <div className="account-readout">
                <span>Organization</span>
                <code>{user.organization_id}</code>
              </div>
            </div>

            <div className="account-inline-actions">
              <button
                type="button"
                disabled={
                  savingProfile
                  || fullName.trim() === user.full_name
                }
                onClick={saveProfile}
              >
                {savingProfile
                  ? "Saving..."
                  : "Save identity"}
              </button>

              <span>
                Email {user.email_verified
                  ? "verified"
                  : "not verified"}
              </span>

              {user.google_connected ? (
                <span>Google connected</span>
              ) : null}

              {user.github_connected ? (
                <span>GitHub connected</span>
              ) : null}
            </div>
          </section>

          <section id="account-sessions">
            <header>
              <span>02</span>
              <div>
                <h3>Active sessions</h3>
                <p>
                  Authentication sessions with access
                  to this account.
                </p>
              </div>
            </header>

            {loading ? (
              <p aria-busy="true">
                Reading active sessions...
              </p>
            ) : sessions.length === 0 ? (
              <p>No active sessions were returned.</p>
            ) : (
              <div className="account-session-list">
                {sessions.map((session) => (
                  <article
                    key={session.id}
                    className="account-session-row"
                    data-current={session.is_current}
                  >
                    <div>
                      <strong>
                        {sessionClient(
                          session.user_agent,
                        )}
                      </strong>

                      <span>
                        {session.is_current
                          ? "Current session"
                          : "Active session"}
                      </span>
                    </div>

                    <dl>
                      <div>
                        <dt>IP</dt>
                        <dd>
                          {session.ip_address ?? "--"}
                        </dd>
                      </div>

                      <div>
                        <dt>Last activity</dt>
                        <dd>
                          {formatTime(
                            session.last_used_at,
                          )}
                        </dd>
                      </div>

                      <div>
                        <dt>Expires</dt>
                        <dd>
                          {formatTime(
                            session.expires_at,
                          )}
                        </dd>
                      </div>
                    </dl>

                    <button
                      type="button"
                      disabled={busyId !== null}
                      onClick={() =>
                        setPendingAction({ type: "revoke-session", session })
                      }
                    >
                      {busyId === session.id
                        ? "Revoking..."
                        : "Revoke"}
                    </button>
                  </article>
                ))}
              </div>
            )}

            {currentSession ? (
              <p className="account-security-note">
                Revoking the current session signs this
                browser out immediately.
              </p>
            ) : null}
          </section>

          <section id="account-password">
            <header>
              <span>03</span>
              <div>
                <h3>Password</h3>
                <p>
                  {passwordLoginEnabled
                    ? "Changing the password invalidates all active sessions and requires sign-in again."
                    : "This account does not have a password credential. Use a connected identity provider to sign in."}
                </p>
              </div>
            </header>

            {passwordLoginEnabled ? (
              <>
                <div className="account-password-form">
                  <label>
                    <span>Current password</span>
                    <input
                      type="password"
                      autoComplete="current-password"
                      value={currentPassword}
                      onChange={(event) =>
                        setCurrentPassword(
                          event.target.value,
                        )
                      }
                    />
                  </label>

                  <label>
                    <span>New password</span>
                    <input
                      type="password"
                      autoComplete="new-password"
                      value={newPassword}
                      onChange={(event) =>
                        setNewPassword(
                          event.target.value,
                        )
                      }
                    />
                  </label>

                  <label>
                    <span>Confirm new password</span>
                    <input
                      type="password"
                      autoComplete="new-password"
                      value={confirmPassword}
                      onChange={(event) =>
                        setConfirmPassword(
                          event.target.value,
                        )
                      }
                    />
                  </label>
                </div>

                <button
                  type="button"
                  disabled={
                    changingPassword
                    || !currentPassword
                    || !newPassword
                    || !confirmPassword
                  }
                  onClick={handlePasswordChange}
                >
                  {changingPassword
                    ? "Changing password..."
                    : "Change password"}
                </button>
              </>
            ) : (
              <p className="account-security-note" role="status">
                Password change is unavailable because this identity has
                no password credential. Continue using your existing sign-in
                method.
              </p>
            )}
          </section>

          {user.role === "admin" ? (
            <section id="account-api">
            <header>
              <span>04</span>
              <div>
                <h3>Organization API access</h3>
                <p>
                  Credentials for programmatic access to
                  this organization.
                </p>
              </div>
            </header>

            {createdKey ? (
              <div className="api-key-reveal">
                <span>New credential</span>
                <strong>
                  Copy this key now. It will not be
                  shown again.
                </strong>
                <code id="sde-created-api-key">{createdKey.key}</code>
                <button
                  type="button"
                  onClick={() => {
                    void handleCopyCreatedKey();
                  }}
                >
                  {keyCopied ? "Copied" : "Copy key"}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    selectCreatedKeyText();
                  }}
                >
                  Select key
                </button>
              </div>
            ) : null}

            <div className="api-key-create">
              <label>
                <span>Credential name</span>
                <input
                  value={keyName}
                  maxLength={255}
                  placeholder="e.g. ingestion-worker"
                  onChange={(event) =>
                    setKeyName(event.target.value)
                  }
                />
              </label>

              <label>
                <input
                  type="checkbox"
                  checked={keyRead}
                  onChange={(event) =>
                    setKeyRead(event.target.checked)
                  }
                />
                Read
              </label>

              <label>
                <input
                  type="checkbox"
                  checked={keyWrite}
                  onChange={(event) =>
                    setKeyWrite(event.target.checked)
                  }
                />
                Write
              </label>

              <button
                type="button"
                disabled={
                  creatingKey
                  || !keyName.trim()
                  || (!keyRead && !keyWrite)
                }
                onClick={handleCreateKey}
              >
                {creatingKey
                  ? "Creating..."
                  : "Create credential"}
              </button>
            </div>

            {!loading && apiKeys.length === 0 ? (
              <p>No active API credentials.</p>
            ) : (
              <div className="api-key-list">
                {apiKeys.map((key) => (
                  <article key={key.id}>
                    <div>
                      <strong>{key.name}</strong>
                      <code>{key.id}</code>
                    </div>

                    <div>
                      <span>
                        Created {formatTime(key.created_at)}
                      </span>
                      <span>
                        Last used {formatTime(key.last_used_at)}
                      </span>
                    </div>

                    <button
                      type="button"
                      disabled={busyId !== null}
                      onClick={() =>
                        setPendingAction({ type: "revoke-key", key })
                      }
                    >
                      {busyId === key.id
                        ? "Revoking..."
                        : "Revoke"}
                    </button>
                  </article>
                ))}
              </div>
            )}
          </section>
          ) : null}

          <section id="account-data">
            <header>
              <span>{user.role === "admin" ? "05" : "04"}</span>
              <div>
                <h3>Account data</h3>
                <p>
                  Export identity and consent records or
                  deactivate account access.
                </p>
              </div>
            </header>

            <div className="account-data-actions">
              <div>
                <strong>Portable account record</strong>
                <p>
                  Download your profile and consent records
                  as JSON. Sources and evidence are excluded.
                </p>
                <button
                  type="button"
                  onClick={handleExport}
                >
                  Export identity data
                </button>
              </div>

              <div className="account-danger">
                <strong>Revoke every session</strong>
                <p>
                  Immediately invalidate authentication
                  on every device, including this one.
                </p>
                <button
                  type="button"
                  disabled={busyId !== null}
                  onClick={() => setPendingAction({ type: "revoke-all" })}
                >
                  Revoke all sessions
                </button>
              </div>

              <div className="account-danger">
                <strong>Deactivate account</strong>
                <p>
                  Disable sign-in and revoke your sessions.
                  Organization sources, evidence, API keys
                  and audit records remain available.
                </p>
                <button
                  type="button"
                  disabled={busyId !== null}
                  onClick={() => setPendingAction({ type: "delete-account" })}
                >
                  Deactivate account
                </button>
              </div>
            </div>
          </section>
        </div>
      </div>

      {pendingAction ? (
        <ConfirmAction
          title={
            pendingAction.type === "revoke-session"
              ? "Revoke authentication session"
              : pendingAction.type === "revoke-key"
                ? "Revoke API credential"
                : pendingAction.type === "revoke-all"
                  ? "Revoke every session"
                  : "Deactivate account"
          }
          description={
            pendingAction.type === "revoke-session"
              ? pendingAction.session.is_current
                ? "This is the current browser session. Revoking it will sign you out immediately."
                : "This session will immediately lose access to the account."
              : pendingAction.type === "revoke-key"
                ? `Credential "${pendingAction.key.name}" will stop working immediately.`
                : pendingAction.type === "revoke-all"
                  ? "Every authentication session will be invalidated, including this browser."
                  : "This disables sign-in, disconnects linked providers and revokes your sessions. Organization sources, snapshots, evidence, API keys and audit records are not deleted."
          }
          confirmLabel={
            pendingAction.type === "revoke-session"
              ? "Revoke session"
              : pendingAction.type === "revoke-key"
                ? "Revoke credential"
                : pendingAction.type === "revoke-all"
                  ? "Revoke all sessions"
                  : "Deactivate account"
          }
          confirmationText={
            pendingAction.type === "delete-account"
              ? "DEACTIVATE"
              : undefined
          }
          busy={busyId !== null}
          onCancel={() => {
            if (busyId === null) {
              setPendingAction(null);
            }
          }}
          onConfirm={() => {
            const action = pendingAction;

            if (action.type === "revoke-session") {
              void handleRevokeSession(action.session).then((succeeded) => {
                if (succeeded) {
                  setPendingAction(null);
                }
              });
              return;
            }

            if (action.type === "revoke-key") {
              void handleRevokeKey(action.key).then((succeeded) => {
                if (succeeded) {
                  setPendingAction(null);
                }
              });
              return;
            }

            if (action.type === "revoke-all") {
              void handleRevokeAll();
              return;
            }

            void handleDeleteAccount();
          }}
        />
      ) : null}
    </section>
  );
}
