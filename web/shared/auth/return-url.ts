import { stripBasePath, withBasePath } from "../base-path";

const RETURN_URL_BASE = "https://deepmentor.invalid";

export interface BrowserLocationParts {
  pathname: string;
  search?: string;
  hash?: string;
}

/**
 * Return a same-origin application path or the supplied safe fallback.
 *
 * The result is APP-RELATIVE (deployment prefix stripped) so it can be fed
 * straight to `router.push`/`router.replace`, which apply the basePath
 * themselves — storing the prefixed form would double it up
 * (`/deepmentor/deepmentor/chat`).
 */
export function normalizeInternalReturnPath(
  raw: string | null | undefined,
  fallback = "/",
): string {
  const candidate = String(raw ?? "").trim();
  if (
    !candidate.startsWith("/") ||
    candidate.startsWith("//") ||
    candidate.includes("\\") ||
    /[\u0000-\u001f\u007f]/.test(candidate)
  ) {
    return fallback;
  }

  try {
    const parsed = new URL(candidate, RETURN_URL_BASE);
    if (parsed.origin !== RETURN_URL_BASE) return fallback;
    const decodedPath = decodeURIComponent(parsed.pathname);
    if (
      decodedPath.startsWith("//") ||
      decodedPath.includes("\\") ||
      /[\u0000-\u001f\u007f]/.test(decodedPath)
    ) {
      return fallback;
    }
    // Strip the prefix from the pathname alone: a combined string would
    // miss the edge case pathname === base ("/deepmentor?x#y").
    const stripped = `${stripBasePath(parsed.pathname)}${parsed.search}${parsed.hash}`;
    // Re-run the leading-slash guard after stripping; the fallback stays
    // reachable when something unexpected survives normalization.
    return stripped.startsWith("/") && !stripped.startsWith("//")
      ? stripped
      : fallback;
  } catch {
    return fallback;
  }
}

export function browserReturnPath(location: BrowserLocationParts): string {
  return normalizeInternalReturnPath(
    `${location.pathname}${location.search ?? ""}${location.hash ?? ""}`,
  );
}

/**
 * The login URL for a raw `window.location.href` assignment. That bypasses
 * the Next router, so the deployment prefix has to be applied here; the
 * `next` parameter stays app-relative (see normalizeInternalReturnPath).
 */
export function loginHref(returnPath: string): string {
  const query = new URLSearchParams({
    next: normalizeInternalReturnPath(returnPath),
  });
  return withBasePath(`/login?${query.toString()}`);
}

/**
 * Server redirects cannot read a fragment. Browsers retain it on the login
 * URL, so carry that fragment into a validated destination that lacks one.
 */
export function inheritLoginHash(returnPath: string, loginHash: string): string {
  const safe = normalizeInternalReturnPath(returnPath);
  if (!loginHash || safe.includes("#")) return safe;
  const hash = loginHash.startsWith("#") ? loginHash : `#${loginHash}`;
  return normalizeInternalReturnPath(`${safe}${hash}`);
}
