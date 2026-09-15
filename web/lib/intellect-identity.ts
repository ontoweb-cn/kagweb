import { apiFetch, apiUrl } from "@/lib/api";

/**
 * The current user's link to an Intellect account on the configured agent
 * service.
 *
 * Personal credential state, not deployment configuration: it is served by the
 * same endpoint family as the agent-loop settings but deliberately not behind
 * the admin gate, and the token itself is never part of any response — only
 * whether a link exists and which member it belongs to.
 */
export type IntellectIdentityStatus = {
  /** False when no agent service is configured, so the card does not apply. */
  available: boolean;
  linked: boolean;
  member_id?: string | null;
  team_id?: string | null;
  project_id?: string | null;
  expires_at?: number | null;
  linked_at?: number | null;
  /** The link cannot be used as-is and needs reconnecting. */
  stale: boolean;
  /**
   * Why, when ``stale`` is set. A link is bound to the service it was minted
   * against, so a deployment that has since moved also lands here — the user
   * sees "reconnect" rather than a turn that fails for no visible reason.
   */
  stale_reason?: "expired" | "service_changed" | null;
};

const ENDPOINT = "/api/settings/agent-loop/identity";

/** Raised with a message already suitable for display. */
export class IntellectIdentityError extends Error {}

async function failureMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string" && body.detail.trim()) return body.detail;
  } catch {
    // Fall through to the generic message; a non-JSON error body is still an
    // error, and the status alone is more useful than a parse failure.
  }
  return `Request failed (${response.status})`;
}

async function call(
  init: RequestInit,
  onOk: (response: Response) => Promise<IntellectIdentityStatus>,
): Promise<IntellectIdentityStatus> {
  const response = await apiFetch(apiUrl(ENDPOINT), init);
  if (!response.ok) throw new IntellectIdentityError(await failureMessage(response));
  return onOk(response);
}

export function getIntellectIdentity(): Promise<IntellectIdentityStatus> {
  return call({ method: "GET" }, (response) => response.json());
}

export function linkIntellectIdentity(payload: {
  login_name?: string;
  password?: string;
  token?: string;
}): Promise<IntellectIdentityStatus> {
  return call(
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
    (response) => response.json(),
  );
}

export function unlinkIntellectIdentity(): Promise<IntellectIdentityStatus> {
  return call({ method: "DELETE" }, (response) => response.json());
}
