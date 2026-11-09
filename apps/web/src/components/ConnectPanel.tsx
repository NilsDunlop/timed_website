import { useCallback, useEffect, useState } from "react";

import { accessIssues, accessMessage, collectionIssue } from "../lib/accessIssues";
import type { components } from "../lib/api/connector-schema";
import {
  apiErrorMessage,
  connectorClient,
  webClientHeaders,
} from "../lib/api/client";
import {
  dataSelectionHref,
  initialCollection,
  notifyCollectionSelection,
  readCollectionPreference,
  writeCollectionPreference,
} from "../lib/collectionNavigation";
import {
  initialUsername,
  isUsername,
  readUsername,
  writeUsername,
} from "../lib/connectUsername";

type Profile = components["schemas"]["Profile"];
type Session = components["schemas"]["SessionSnapshot"];

const idleSession: Session = { state: "disconnected" };

function preferredCollectionId(profile: Profile): string {
  const collections = profile.collections ?? [];
  let rememberedId: string | null = null;
  try {
    rememberedId = readCollectionPreference(window.localStorage, profile.id);
  } catch {
    // Browser storage is optional. Fall back to the configured default.
  }
  return initialCollection(collections, null, rememberedId)?.id ?? "";
}

function preferredUsername(profile: Profile | undefined): string {
  if (!profile || profile.kind !== "slurm") return "";
  let remembered: string | null = null;
  try {
    remembered = readUsername(window.localStorage, profile.id);
  } catch {
    // Browser storage is optional. Fall back to the profile's own username.
  }
  return initialUsername(remembered, profile.username);
}

export default function ConnectPanel() {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [profileId, setProfileId] = useState("");
  const [collectionId, setCollectionId] = useState("");
  const [username, setUsername] = useState("");
  const [session, setSession] = useState<Session>(idleSession);
  const [answers, setAnswers] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const promptKey = (session.prompts ?? [])
    .map((prompt) => prompt.prompt + ":" + String(prompt.echo))
    .join("|");

  const refreshSession = useCallback(async () => {
    const result = await connectorClient.GET("/connector/session");
    if (!result.data) {
      throw new Error(apiErrorMessage(result.error, "Could not read connector session"));
    }
    setSession(result.data);
  }, []);

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const [profilesResult, sessionResult] = await Promise.all([
          connectorClient.GET("/connector/profiles"),
          connectorClient.GET("/connector/session"),
        ]);
        if (!profilesResult.data || !sessionResult.data) {
          throw new Error(
            apiErrorMessage(
              profilesResult.error ?? sessionResult.error,
              "Could not reach the local connector",
            ),
          );
        }
        if (!active) return;
        setProfiles(profilesResult.data);
        const nextProfileId =
          sessionResult.data.profile_id ?? profilesResult.data.at(0)?.id ?? "";
        const nextProfile = profilesResult.data.find(
          (profile) => profile.id === nextProfileId,
        );
        setProfileId(nextProfileId);
        setCollectionId(nextProfile ? preferredCollectionId(nextProfile) : "");
        setUsername(preferredUsername(nextProfile));
        setSession(sessionResult.data);
      } catch (reason) {
        if (active) {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (session.state === "disconnected") return;
    const timer = window.setInterval(() => {
      void refreshSession().catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : String(reason));
      });
    }, 1000);
    return () => window.clearInterval(timer);
  }, [refreshSession, session.state]);

  useEffect(() => {
    setAnswers((session.prompts ?? []).map(() => ""));
  }, [promptKey]);

  async function mutate(action: () => Promise<Session | undefined>) {
    setBusy(true);
    setError("");
    try {
      const next = await action();
      if (next) setSession(next);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function connect() {
    const login = needsUsername ? username.trim() : null;
    await mutate(async () => {
      const result = await connectorClient.POST("/connector/connect", {
        headers: webClientHeaders,
        body: login ? { profile_id: profileId, username: login } : { profile_id: profileId },
      });
      if (!result.data) {
        throw new Error(apiErrorMessage(result.error, "Could not start connection"));
      }
      if (login) {
        try {
          writeUsername(window.localStorage, profileId, login);
        } catch {
          // Browser storage is optional. The username is asked again next time.
        }
      }
      return result.data;
    });
  }

  async function respond(
    body: components["schemas"]["RespondRequest"],
  ) {
    await mutate(async () => {
      const result = await connectorClient.POST("/connector/respond", {
        headers: webClientHeaders,
        body,
      });
      if (!result.data) {
        throw new Error(apiErrorMessage(result.error, "Connector rejected response"));
      }
      setAnswers([]);
      return result.data;
    });
  }

  async function disconnect() {
    await mutate(async () => {
      const result = await connectorClient.POST("/connector/disconnect", {
        headers: webClientHeaders,
        body: { cancel_job: true },
      });
      if (!result.data) {
        throw new Error(apiErrorMessage(result.error, "Could not disconnect"));
      }
      return result.data;
    });
  }

  function chooseProfile(nextProfileId: string) {
    const nextProfile = profiles.find((profile) => profile.id === nextProfileId);
    const nextCollectionId = nextProfile ? preferredCollectionId(nextProfile) : "";
    setProfileId(nextProfileId);
    setCollectionId(nextCollectionId);
    setUsername(preferredUsername(nextProfile));
    notifyCollectionSelection(nextProfileId, nextCollectionId);
  }

  function chooseCollection(nextCollectionId: string) {
    setCollectionId(nextCollectionId);
    notifyCollectionSelection(profileId, nextCollectionId);
    try {
      writeCollectionPreference(
        window.localStorage,
        profileId,
        nextCollectionId,
      );
    } catch {
      // Browser storage is optional. Keep the choice for this page session.
    }
  }

  const connected = session.state === "connected";
  const active = session.state !== "disconnected" && session.state !== "error";
  const activeProfile = profiles.find(
    (profile) => profile.id === (session.profile_id ?? profileId),
  );
  const selectedProfile = profiles.find((profile) => profile.id === profileId);
  const profileCollections = selectedProfile?.collections ?? [];
  const selectedCollection =
    profileCollections.find((collection) => collection.id === collectionId) ??
    null;
  const localMode = activeProfile?.kind === "local";
  const needsUsername = selectedProfile?.kind === "slurm";
  const typedUsername = username.trim();
  const usernameProblem =
    needsUsername && typedUsername && !isUsername(typedUsername)
      ? "Enter only the login name: letters, digits, '.', '_' or '-'."
      : "";
  const usernameReady = !needsUsername || (typedUsername !== "" && !usernameProblem);
  const canConnect = Boolean(profileId) && !busy && usernameReady;
  // What this account can't read, reported by the data server once it runs.
  const issues = connected ? accessIssues(session.handshake) : [];
  const issueCollections = activeProfile?.collections ?? [];

  return (
    <div className="stack">
      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}

      <div className="form-row">
        <label htmlFor="profile">Compute profile</label>
        <select
          id="profile"
          name="profile"
          value={profileId}
          disabled={active || busy}
          onChange={(event) => chooseProfile(event.target.value)}
        >
          {profiles.map((profile) => (
            <option key={profile.id} value={profile.id}>
              {profile.name}
            </option>
          ))}
        </select>
        {!active ? (
          <button type="button" disabled={!canConnect} onClick={connect}>
            {session.state === "error" ? "Reconnect" : "Connect"}
          </button>
        ) : (
          <button type="button" className="secondary" disabled={busy} onClick={disconnect}>
            {localMode ? "Disconnect and stop server" : "Disconnect and cancel job"}
          </button>
        )}
      </div>

      {profileCollections.length > 0 && (
        <div className="stack">
          <label className="dataset-selector-control">
            <span>Dataset</span>
            <select
              name="dataset"
              value={collectionId}
              disabled={busy}
              onChange={(event) => chooseCollection(event.target.value)}
            >
              {profileCollections.map((collection) => (
                <option key={collection.id} value={collection.id}>
                  {collection.title}
                  {collectionIssue(issues, collection.path) ? " (unavailable)" : ""}
                </option>
              ))}
            </select>
          </label>
          {selectedCollection?.description && (
            <p className="dataset-description">{selectedCollection.description}</p>
          )}
        </div>
      )}

      {/* The login steps, in order: username, host key, password and code. */}
      {needsUsername && !active && (
        <div className="stack">
          <label className="username-control">
            <span>Username</span>
            <input
              name="username"
              autoComplete="username"
              spellCheck={false}
              value={username}
              disabled={busy}
              aria-describedby="username-help"
              onChange={(event) => setUsername(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && canConnect) void connect();
              }}
            />
          </label>
          <p id="username-help" className="field-help">
            {usernameProblem || "The password and verification code are asked after Connect."}
          </p>
        </div>
      )}

      {session.state === "host_key_confirm" && (
        <section className="subpanel" aria-labelledby="host-key-heading">
          <h3 id="host-key-heading">Confirm SSH host key</h3>
          <p>Compare this fingerprint with a trusted terminal session:</p>
          <code className="fingerprint">{session.host_key_fingerprint}</code>
          <div className="button-row">
            <button type="button" disabled={busy} onClick={() => respond({ accept_host_key: true })}>
              Accept host key
            </button>
            <button
              type="button"
              className="secondary"
              disabled={busy}
              onClick={() => respond({ accept_host_key: false })}
            >
              Reject
            </button>
          </div>
        </section>
      )}

      {session.state === "awaiting_input" && (
        <form
          className="subpanel stack"
          autoComplete="off"
          onSubmit={(event) => {
            event.preventDefault();
            void respond({ answers });
          }}
        >
          <h3>SSH authentication</h3>
          {(session.prompts ?? []).map((prompt, index) => (
            <label key={prompt.prompt + String(index)}>
              <span>{prompt.prompt}</span>
              <input
                autoComplete="off"
                type={prompt.echo ? "text" : "password"}
                value={answers[index] ?? ""}
                onChange={(event) =>
                  setAnswers((current) =>
                    current.map((value, answerIndex) =>
                      answerIndex === index ? event.target.value : value,
                    ),
                  )
                }
              />
            </label>
          ))}
          <button type="submit" disabled={busy || answers.some((answer) => !answer)}>
            Submit
          </button>
        </form>
      )}

      {session.state === "choose_existing_job" && (
        <section className="subpanel">
          <h3>Existing TIMED job</h3>
          <p>
            Reuse a running server, or cancel it and submit a fresh job. A reused
            server keeps the settings it started with.
          </p>
          <ul className="plain-list">
            {(session.existing_jobs ?? []).map((job) => (
              <li key={job.job_id} className="job-choice">
                <span>
                  <strong>{job.job_id}</strong> · {job.state}
                  {job.node ? " · " + job.node : ""}
                </span>
                <span className="button-row">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      respond({
                        existing_job: { action: "reuse", job_id: job.job_id },
                      })
                    }
                  >
                    Reuse
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    disabled={busy}
                    onClick={() =>
                      respond({
                        existing_job: { action: "cancel", job_id: job.job_id },
                      })
                    }
                  >
                    Cancel and replace
                  </button>
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <dl className="status-grid" aria-live="polite">
        <div>
          <dt>Status</dt>
          <dd data-testid="session-state">{session.state.replaceAll("_", " ")}</dd>
        </div>
        {localMode && connected && (
          <div>
            <dt>Data server</dt>
            <dd>127.0.0.1:{session.local_forward_port}</dd>
          </div>
        )}
        {session.job_id && (
          <div>
            <dt>Slurm job</dt>
            <dd>{session.job_id}</dd>
          </div>
        )}
        {session.slurm_state && (
          <div>
            <dt>Queue state</dt>
            <dd>{session.slurm_state}</dd>
          </div>
        )}
        {session.pending_reason && (
          <div>
            <dt>Pending reason</dt>
            <dd>{session.pending_reason}</dd>
          </div>
        )}
        {session.node && (
          <div>
            <dt>Compute node</dt>
            <dd>{session.node}</dd>
          </div>
        )}
      </dl>

      {session.state === "error" && (
        <div className="notice error" role="alert">
          <strong>{session.message ?? "Connection failed"}</strong>
          {session.hint && <p>{session.hint}</p>}
        </div>
      )}

      {connected && (
        <div className="notice success">
          Connected to {String(session.handshake?.hostname ?? "the data server")}.{" "}
          <a href={dataSelectionHref(selectedCollection?.id ?? null)}>
            Browse data
          </a>
        </div>
      )}

      {issues.length > 0 && (
        <div className="notice warning" role="status" data-testid="access-issues">
          <strong>Some data isn't available to your account</strong>
          <ul className="access-issues">
            {issues.map((issue, index) => (
              <li key={`${issue.kind}-${index}`}>{accessMessage(issue, issueCollections)}</li>
            ))}
          </ul>
          <p>Ask the data owner for access, then disconnect and connect again.</p>
        </div>
      )}

      {(session.logs ?? []).length > 0 && (
        <details open>
          <summary>Session log</summary>
          <pre className="log-tail">{session.logs?.join("\n")}</pre>
        </details>
      )}
    </div>
  );
}
