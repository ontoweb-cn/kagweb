/**
 * The `/chat` launch-URL contract — both ends in one place.
 *
 * Capability shortcuts can open `/chat` with query parameters that set up the
 * composer before the user types. Parsing is deliberately dependency-free.
 */

/** Composer setup requested by the URL that opened `/chat`. */
export interface ChatLaunchIntent {
  /** Capability to activate. `""` means plain chat; `null` means unspecified. */
  capability: string | null;
}

const EMPTY_INTENT: ChatLaunchIntent = {
  capability: null,
};

/** Read the launch intent out of a `location.search` string. */
export function readChatLaunchIntent(search: string): ChatLaunchIntent {
  if (!search) return { ...EMPTY_INTENT };
  const params = new URLSearchParams(search);
  const capability = params.get("capability");
  return {
    capability: capability === null ? null : capability.trim(),
  };
}
