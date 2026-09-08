"use client";

import { useEffect, useState } from "react";
import {
  KeyRound,
  Save,
  ShieldOff,
  SlidersHorizontal,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import {
  getGuardianRestrictions,
  listGuardianRelationships,
  resetLearnerCredentials,
  revokeMyGuardianRelationship,
  saveGuardianRestrictions,
  type GuardianExtension,
  type GuardianRelationship,
  type GuardianRestrictions,
} from "@/lib/guardian-api";

type BusyAction =
  | "loading"
  | "restrictions"
  | "credentials"
  | "revoke"
  | null;
type ConfirmAction = "credentials" | "revoke" | null;

const permissionKeys: Record<string, string> = {
  assign_materials: "Manage materials",
  manage_restrictions: "Manage restrictions",
  reset_credentials: "Reset credentials",
  view_reports: "View reports",
};

export default function GuardianSettingsPage() {
  const { t } = useTranslation();
  const [relationships, setRelationships] = useState<GuardianRelationship[]>(
    [],
  );
  const [selectedRelationship, setSelectedRelationship] =
    useState<GuardianRelationship | null>(null);
  const [restrictions, setRestrictions] = useState<GuardianRestrictions | null>(
    null,
  );
  const [extensions, setExtensions] = useState<GuardianExtension[]>([]);
  const [newPassword, setNewPassword] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<BusyAction>(null);
  const [confirmAction, setConfirmAction] = useState<ConfirmAction>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void listGuardianRelationships()
      .then((value) => {
        if (!cancelled) setRelationships(value);
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError((reason as Error).message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const can = (permission: string) =>
    selectedRelationship?.permissions.includes(permission) ?? false;

  const selectLearner = async (relationship: GuardianRelationship) => {
    setSelectedRelationship(relationship);
    setRestrictions(null);
    setExtensions([]);
    setNewPassword("");
    setMessage(null);
    setError(null);
    setBusy("loading");
    try {
      const nextRestrictions = relationship.permissions.includes(
        "manage_restrictions",
      )
        ? await getGuardianRestrictions(relationship.learner_user_id)
        : null;
      if (nextRestrictions) {
        setRestrictions(nextRestrictions.restrictions);
        setExtensions(nextRestrictions.available_extensions);
      }
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const saveRestrictions = async () => {
    if (!selectedRelationship || !restrictions || !can("manage_restrictions")) {
      return;
    }
    setBusy("restrictions");
    setError(null);
    setMessage(null);
    try {
      setRestrictions(
        await saveGuardianRestrictions(
          selectedRelationship.learner_user_id,
          restrictions,
        ),
      );
      setMessage(t("Learning restrictions saved."));
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const resetCredentials = async () => {
    if (
      !selectedRelationship ||
      !can("reset_credentials") ||
      newPassword.length < 8
    ) {
      return;
    }
    setConfirmAction(null);
    setBusy("credentials");
    setError(null);
    setMessage(null);
    try {
      await resetLearnerCredentials(
        selectedRelationship.learner_user_id,
        newPassword,
      );
      setNewPassword("");
      setMessage(t("Learner credentials were reset."));
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const revokeRelationship = async () => {
    if (!selectedRelationship) return;
    setConfirmAction(null);
    setBusy("revoke");
    setError(null);
    setMessage(null);
    try {
      await revokeMyGuardianRelationship(selectedRelationship.id);
      setRelationships((current) =>
        current.filter((item) => item.id !== selectedRelationship.id),
      );
      setSelectedRelationship(null);
      setMessage(t("Guardian access revoked."));
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <main className="mx-auto max-w-3xl px-6 py-8">
      <h1 className="text-xl font-semibold">{t("Guardian management")}</h1>
      <p className="mt-1 text-sm text-[var(--muted-foreground)]">
        {t("Review authorized learners and their approved learning materials.")}
      </p>

      {error && (
        <p className="mt-4 rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-600">
          {error}
        </p>
      )}
      {message && (
        <p className="mt-4 rounded-lg bg-emerald-500/10 px-3 py-2 text-sm text-emerald-700 dark:text-emerald-400">
          {message}
        </p>
      )}

      {loading ? (
        <p className="mt-8 text-sm text-[var(--muted-foreground)]">
          {t("Loading guardian relationships…")}
        </p>
      ) : relationships.length === 0 ? (
        <p className="mt-8 text-sm text-[var(--muted-foreground)]">
          {t("No active learner relationships.")}
        </p>
      ) : (
        <div className="mt-6 grid gap-3 sm:grid-cols-2">
          {relationships.map((relationship) => (
            <button
              key={relationship.id}
              type="button"
              onClick={() => void selectLearner(relationship)}
              className={`rounded-lg border p-4 text-left transition-colors hover:bg-[var(--muted)] ${
                selectedRelationship?.id === relationship.id
                  ? "border-[var(--primary)] bg-[var(--muted)]"
                  : "border-[var(--border)]"
              }`}
            >
              <strong>{relationship.learner_username}</strong>
              <span className="mt-1 block text-xs text-[var(--muted-foreground)]">
                {relationship.permissions
                  .map((permission) =>
                    t(permissionKeys[permission] ?? permission),
                  )
                  .join(" · ")}
              </span>
            </button>
          ))}
        </div>
      )}

      {selectedRelationship && (
        <section className="mt-8 space-y-6 border-t border-[var(--border)] pt-6">
          <div className="flex items-start justify-between gap-3">
            <h2 className="font-medium">
              {selectedRelationship.learner_username}
            </h2>
            <button
              type="button"
              onClick={() => setConfirmAction("revoke")}
              disabled={busy !== null}
              className="inline-flex items-center gap-1.5 rounded-md border border-red-500/40 px-3 py-1.5 text-xs text-red-600 disabled:opacity-50"
            >
              <ShieldOff className="h-3.5 w-3.5" />
              {t("Revoke access")}
            </button>
          </div>

          {busy === "loading" ? (
            <p className="text-sm text-[var(--muted-foreground)]">
              {t("Loading learner details…")}
            </p>
          ) : null}

          {can("manage_restrictions") && restrictions && (
            <div className="rounded-lg border border-[var(--border)] p-4">
              <div className="mb-3 flex items-center justify-between gap-3">
                <h3 className="inline-flex items-center gap-2 text-sm font-medium">
                  <SlidersHorizontal className="h-4 w-4" />
                  {t("Learning restrictions")}
                </h3>
                <button
                  type="button"
                  onClick={() => void saveRestrictions()}
                  disabled={busy !== null}
                  className="inline-flex items-center gap-1.5 rounded-md bg-[var(--primary)] px-3 py-1.5 text-xs text-[var(--primary-foreground)] disabled:opacity-50"
                >
                  <Save className="h-3.5 w-3.5" />
                  {busy === "restrictions"
                    ? t("Saving…")
                    : t("Save restrictions")}
                </button>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="text-xs">
                  <span className="mb-1 block text-[var(--muted-foreground)]">
                    {t("Age band")}
                  </span>
                  <select
                    value={restrictions.age_band}
                    disabled={busy !== null}
                    onChange={(event) =>
                      setRestrictions((current) =>
                        current
                          ? {
                              ...current,
                              age_band: event.target
                                .value as GuardianRestrictions["age_band"],
                            }
                          : current,
                      )
                    }
                    className="h-9 w-full rounded-md border border-[var(--border)] bg-[var(--background)] px-2"
                  >
                    {(["6-8", "9-12", "13-15"] as const).map((band) => (
                      <option key={band} value={band}>
                        {band}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex items-center gap-2 self-end rounded-md border border-[var(--border)] px-3 py-2 text-xs">
                  <input
                    type="checkbox"
                    checked={restrictions.allow_upload}
                    disabled={busy !== null}
                    onChange={(event) =>
                      setRestrictions((current) =>
                        current
                          ? { ...current, allow_upload: event.target.checked }
                          : current,
                      )
                    }
                  />
                  {t("Allow learner uploads")}
                </label>
              </div>
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                {extensions.map((extension) => (
                  <label
                    key={extension.id}
                    className="flex items-center gap-2 rounded-md border border-[var(--border)] px-3 py-2 text-xs"
                  >
                    <input
                      type="checkbox"
                      checked={restrictions.extensions.includes(extension.id)}
                      disabled={busy !== null}
                      onChange={() =>
                        setRestrictions((current) =>
                          current
                            ? {
                                ...current,
                                extensions: current.extensions.includes(
                                  extension.id,
                                )
                                  ? current.extensions.filter(
                                      (id) => id !== extension.id,
                                    )
                                  : [...current.extensions, extension.id],
                              }
                            : current,
                        )
                      }
                    />
                    <span>{extension.name}</span>
                  </label>
                ))}
              </div>
            </div>
          )}

          {can("reset_credentials") && (
            <div className="rounded-lg border border-[var(--border)] p-4">
              <label className="text-xs">
                <span className="mb-1 block text-[var(--muted-foreground)]">
                  {t("New learner password")}
                </span>
                <input
                  type="password"
                  value={newPassword}
                  minLength={8}
                  autoComplete="new-password"
                  disabled={busy !== null}
                  onChange={(event) => setNewPassword(event.target.value)}
                  className="h-9 w-full rounded-md border border-[var(--border)] bg-[var(--background)] px-3"
                />
              </label>
              <button
                type="button"
                onClick={() => setConfirmAction("credentials")}
                disabled={busy !== null || newPassword.length < 8}
                className="mt-3 inline-flex items-center gap-2 rounded-md border border-red-500/40 px-3 py-2 text-sm text-red-600 disabled:opacity-50"
              >
                <KeyRound className="h-4 w-4" />
                {busy === "credentials"
                  ? t("Resetting…")
                  : t("Reset learner credentials")}
              </button>
            </div>
          )}
        </section>
      )}

      <ConfirmDialog
        open={confirmAction !== null}
        title={
          confirmAction === "credentials"
            ? t("Reset learner credentials")
            : t("Revoke guardian access")
        }
        confirmLabel={
          confirmAction === "credentials"
            ? t("Reset credentials")
            : t("Revoke access")
        }
        tone="danger"
        busy={busy !== null}
        onCancel={() => setConfirmAction(null)}
        onConfirm={() => {
          if (confirmAction === "credentials") void resetCredentials();
          if (confirmAction === "revoke") void revokeRelationship();
        }}
      >
        {confirmAction === "credentials"
          ? t(
              "This changes the learner password and revokes every learner device credential.",
            )
          : t("This immediately removes your access to this learner account.")}
      </ConfirmDialog>
    </main>
  );
}
