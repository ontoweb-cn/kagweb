/**
 * Subpath deployment support (e.g. `https://ai.wust.edu.cn/deepmentor`).
 *
 * The deployment prefix is a BUILD-time input: Next bakes `basePath` into
 * the bundle from `NEXT_PUBLIC_BASE_PATH` (see the mirrored normalization
 * in `next.config.js` — this CJS config cannot import this TS module, so
 * the two rules must be kept in sync by hand), and the browser-side inlines
 * of the same variable flow through here. Every place that builds a URL
 * outside `Link` / `router.push` (which the framework prefixes
 * automatically) must go through `withBasePath()` — raw `fetch("/api/…")`,
 * `new WebSocket("/ws/…")`, `window.location.href` and native HTML
 * attributes like `<svg><image href>` all resolve against the domain root
 * and would silently drop the prefix.
 *
 * The env read is intentionally per-call (not a module constant) so tests
 * can exercise both prefixed and unprefixed behavior in one process. In
 * production builds Next inlines NEXT_PUBLIC_* everywhere (client AND
 * middleware), so this resolves to the build-time value regardless.
 */

/** Normalized deployment prefix: "" or "/segment" without trailing slash. */
export function getBasePath(): string {
  const raw = String(process.env.NEXT_PUBLIC_BASE_PATH ?? "").trim();
  if (!raw || raw === "/") return "";
  const withSlash = raw.startsWith("/") ? raw : `/${raw}`;
  // A trailing slash would produce "//" joins and redirect loops.
  return withSlash.replace(/\/+$/, "");
}

/** Turn an app-relative path ("/api/x") into the publicly served URL path. */
export function withBasePath(path: string): string {
  const base = getBasePath();
  if (!base || !path.startsWith("/")) return path;
  // Already-prefixed detection must match the path boundary: a plain
  // startsWith(base) would also swallow "/sub-x"-style app paths and skip
  // the prefix (mirrors the strip check below).
  if (path === base || path.startsWith(`${base}/`)) return path;
  if (path === "/") return `${base}/`;
  return `${base}${path}`;
}

/**
 * Turn a publicly served pathname back into the app-relative path the
 * framework reasons about. Unknown prefixes pass through untouched, so
 * calling this on an already-relative path (middleware receives
 * basePath-stripped paths) is a no-op.
 */
export function stripBasePath(pathname: string): string {
  const base = getBasePath();
  if (!base) return pathname;
  if (pathname === base) return "/";
  if (pathname.startsWith(`${base}/`)) return pathname.slice(base.length);
  return pathname;
}
