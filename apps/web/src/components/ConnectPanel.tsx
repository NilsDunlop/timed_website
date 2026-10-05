import { useCallback, useEffect, useState } from "react";

import type { components } from "../lib/api/connector-schema";
import {
  apiErrorMessage,
  connectorClient,
  webClientHeaders,
} from "../lib/api/client";

type Profile = components["schemas"]["Profile"];
type Session = components["schemas"]["SessionSnapshot"];

const idleSession: Session = { state: "disconnected" };

export default function ConnectPanel() {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [profileId, setProfileId] = useState("");
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
        setProfileId(
          sessionResult.data.profile_id ?? profilesResult.data.at(0)?.id ?? "",
        );
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
    await mutate(async () => {
      const result = await connectorClient.POST("/connector/connect", {
        headers: webClientHeaders,
        body: { profile_id: profileId },
      });
      if (!result.data) {
        throw new Error(apiErrorMessage(result.error, "Could not start connection"));
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

  const connected = session.state === "connected";
  const active = session.state !== "disconnected" && session.state !== "error";

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
          onChange={(event) => setProfileId(event.target.value)}
        >
          {profiles.map((profile) => (
            <option key={profile.id} value={profile.id}>
              {profile.name}
            </option>
          ))}
        </select>
        {!active && !connected ? (
          <button type="button" disabled={!profileId || busy} onClick={connect}>
            {session.state === "error" ? "Reconnect" : "Connect"}
          </button>
        ) : (
          <button type="button" className="secondary" disabled={busy} onClick={disconnect}>
            Disconnect and cancel job
          </button>
        )}
      </div>

      <dl className="status-grid" aria-live="polite">
        <div>
          <dt>Status</dt>
          <dd data-testid="session-state">{session.state.replaceAll("_", " ")}</dd>
        </div>
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
          <p>Reuse a running server, or cancel it and submit a fresh job.</p>
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

      {session.state === "error" && (
        <div className="notice error" role="alert">
          <strong>{session.message ?? "Connection failed"}</strong>
          {session.hint && <p>{session.hint}</p>}
        </div>
      )}

      {connected && (
        <div className="notice success">
          Connected to {String(session.handshake?.hostname ?? "the data server")}.{" "}
          <a href="/data">Browse data</a>
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
