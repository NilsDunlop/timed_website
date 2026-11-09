// The login name a Slurm profile connects with. It is remembered per profile in
// browser storage because it is not a secret; passwords, verification codes and
// key passphrases are never stored.

/** The connector's rule for a login name: no spaces, slashes or leading dash. */
export const USERNAME_PATTERN = /^[A-Za-z0-9_][A-Za-z0-9._-]{0,63}$/;

const STORAGE_PREFIX = "timed.connect.username.";

export function isUsername(value: string): boolean {
  return USERNAME_PATTERN.test(value);
}

/** The remembered login name for a profile, ignoring anything malformed. */
export function readUsername(
  storage: Pick<Storage, "getItem">,
  profileId: string,
): string | null {
  const value = storage.getItem(STORAGE_PREFIX + profileId);
  return value !== null && isUsername(value) ? value : null;
}

export function writeUsername(
  storage: Pick<Storage, "setItem">,
  profileId: string,
  username: string,
): void {
  storage.setItem(STORAGE_PREFIX + profileId, username);
}

/** What the Username field starts with: the last one used here, else the profile's. */
export function initialUsername(
  remembered: string | null,
  configured: string | null | undefined,
): string {
  return remembered ?? configured ?? "";
}
