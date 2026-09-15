"use client";

import { useCallback, useEffect, useState } from "react";
import { Link2, Link2Off, TriangleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";

import Button from "@/components/ui/Button";
import {
  getIntellectIdentity,
  IntellectIdentityError,
  linkIntellectIdentity,
  unlinkIntellectIdentity,
  type IntellectIdentityStatus,
} from "@/lib/intellect-identity";

/**
 * Connect the signed-in user's own Intellect account.
 *
 * Each account links for itself: the agent backend authenticates as the
 * deployment by default, and without a link the service cannot tell one KAGWeb
 * user from another — so a user who wants their turns attributed to (and
 * isolated by) their own Intellect account connects it here. Nothing about the
 * link is shared with other users, and the token never reaches the browser.
 */
export function IntellectLinkCard() {
  const { t } = useTranslation();
  const [status, setStatus] = useState<IntellectIdentityStatus | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"password" | "token">("password");
  const [loginName, setLoginName] = useState("");
  const [password, setPassword] = useState("");
  const [token, setToken] = useState("");

  const load = useCallback(async () => {
    try {
      setStatus(await getIntellectIdentity());
    } catch (caught) {
      // A failed read must not look like "not linked": that would invite the
      // user to connect an account that may already be connected.
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const connect = async () => {
    setPending(true);
    setError(null);
    try {
      const next = await linkIntellectIdentity(
        mode === "token" ? { token } : { login_name: loginName, password },
      );
      setStatus(next);
      // Drop the secret from component state the moment it is no longer needed.
      setPassword("");
      setToken("");
    } catch (caught) {
      setError(
        caught instanceof IntellectIdentityError
          ? caught.message
          : t("intellect.link.genericError"),
      );
    } finally {
      setPending(false);
    }
  };

  const disconnect = async () => {
    setPending(true);
    setError(null);
    try {
      setStatus(await unlinkIntellectIdentity());
    } catch (caught) {
      setError(
        caught instanceof IntellectIdentityError
          ? caught.message
          : t("intellect.link.genericError"),
      );
    } finally {
      setPending(false);
    }
  };

  // No agent service configured means there is nothing to link against, and a
  // card that can only fail is worse than no card.
  if (status && !status.available) return null;

  const linked = Boolean(status?.linked);
  const canSubmit =
    !pending && (mode === "token" ? token.trim() !== "" : loginName.trim() !== "" && password !== "");

  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--muted)]/30 p-4">
      <div className="flex items-start gap-3">
        <Link2 className="mt-0.5 h-5 w-5 text-[var(--primary)]" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">{t("intellect.link.title")}</p>
          <p className="mt-1 text-xs text-[var(--muted-foreground)]">
            {t("intellect.link.description")}
          </p>

          {!linked && (
            <p className="mt-1 text-xs text-[var(--muted-foreground)]">
              {t("intellect.link.unlinkedScope")}
            </p>
          )}

          {linked && (
            <p className="mt-3 text-sm text-[var(--foreground)]">
              {t("intellect.link.connectedAs", { member: status?.member_id })}
            </p>
          )}
          {linked && status?.expires_at ? (
            <p className="mt-1 text-xs text-[var(--muted-foreground)]">
              {t("intellect.link.expiresAt", {
                date: new Date(status.expires_at * 1000).toLocaleDateString(),
              })}
            </p>
          ) : null}
          {linked && (status?.team_id || status?.project_id) && (
            <p className="mt-1 text-xs text-[var(--muted-foreground)]">
              {t("intellect.link.scope", {
                team: status?.team_id ?? "—",
                project: status?.project_id ?? "—",
              })}
            </p>
          )}
          {linked && status?.stale && (
            <p className="mt-2 flex items-start gap-1.5 text-xs text-amber-600 dark:text-amber-400">
              <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              {status.stale_reason === "service_changed"
                ? t("intellect.link.staleServiceChanged")
                : t("intellect.link.stale")}
            </p>
          )}

          {error && (
            <p className="mt-3 text-sm text-red-600 dark:text-red-400">{error}</p>
          )}

          {!linked && status !== null && (
            <div className="mt-3 space-y-3">
              <div className="flex gap-2 text-xs">
                <button
                  type="button"
                  className={
                    mode === "password"
                      ? "font-medium text-[var(--foreground)]"
                      : "text-[var(--muted-foreground)]"
                  }
                  onClick={() => setMode("password")}
                >
                  {t("intellect.link.modePassword")}
                </button>
                <button
                  type="button"
                  className={
                    mode === "token"
                      ? "font-medium text-[var(--foreground)]"
                      : "text-[var(--muted-foreground)]"
                  }
                  onClick={() => setMode("token")}
                >
                  {t("intellect.link.modeToken")}
                </button>
              </div>

              {mode === "password" ? (
                <>
                  <input
                    className="h-9 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 text-sm text-[var(--foreground)] outline-none focus:border-[var(--ring)]"
                    placeholder={t("intellect.link.loginPlaceholder")}
                    value={loginName}
                    autoComplete="username"
                    onChange={(event) => setLoginName(event.target.value)}
                  />
                  <input
                    className="h-9 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 text-sm text-[var(--foreground)] outline-none focus:border-[var(--ring)]"
                    placeholder={t("intellect.link.passwordPlaceholder")}
                    type="password"
                    value={password}
                    autoComplete="current-password"
                    onChange={(event) => setPassword(event.target.value)}
                  />
                  <p className="text-xs text-[var(--muted-foreground)]">
                    {t("intellect.link.passwordNotice")}
                  </p>
                </>
              ) : (
                <>
                  <input
                    className="h-9 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 text-sm text-[var(--foreground)] outline-none focus:border-[var(--ring)]"
                    placeholder={t("intellect.link.tokenPlaceholder")}
                    type="password"
                    value={token}
                    autoComplete="off"
                    onChange={(event) => setToken(event.target.value)}
                  />
                  <p className="text-xs text-[var(--muted-foreground)]">
                    {t("intellect.link.tokenNotice")}
                  </p>
                </>
              )}
            </div>
          )}

          <div className="mt-3 flex flex-wrap items-center gap-2">
            {!linked && (
              <Button
                type="button"
                onClick={() => void connect()}
                disabled={!canSubmit}
              >
                {t("intellect.link.connect")}
              </Button>
            )}
            {linked && (
              <Button
                type="button"
                variant="secondary"
                onClick={() => void disconnect()}
                disabled={pending}
              >
                <Link2Off className="mr-1.5 h-3.5 w-3.5" />
                {t("intellect.link.disconnect")}
              </Button>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
