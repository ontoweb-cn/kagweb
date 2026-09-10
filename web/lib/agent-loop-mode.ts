/**
 * Primary-loop picker modes.
 *
 * The picker has two modes that are not profile ids — "let the auto rule
 * choose" and "run the framework-shell stub" — plus one mode per profile id.
 * They are kept as sentinels rather than null/"" so the radio value stays a
 * string; the backend stores ``null`` for auto and ``""`` for the stub.
 */
export const PRIMARY_AUTO = "__auto__";
export const PRIMARY_NONE = "__none__";

/**
 * Map the stored primary onto the picker's mode.
 *
 * The three states are distinct and must not be collapsed by falsiness:
 * ``null``/missing → Automatic, the empty string → the shell stub, anything
 * else → that profile. A stored primary that already equals the auto-resolved
 * one displays as Automatic, because that is what it will resolve to anyway.
 */
export function modeFromPrimary(
  primary: string | null | undefined,
  autoPrimary: string,
): string {
  if (primary === null || primary === undefined) return PRIMARY_AUTO;
  if (primary === "") return PRIMARY_NONE;
  return primary === autoPrimary ? PRIMARY_AUTO : primary;
}
