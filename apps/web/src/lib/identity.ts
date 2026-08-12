/**
 * How to name a person on screen when the identity provider was sparing.
 *
 * An access token carries an email claim only when the app registration asks for
 * one, so `email` can legitimately be null (backlog B-009). The old behaviour was
 * worse than a blank: the API invented `<subject>@unknown.invalid` and the screen
 * displayed it as if it were an address someone could write to.
 *
 * Order of preference: the email, then the display name, then the subject — which
 * is unfriendly but always present, and is the identity the product actually
 * authorizes against.
 */

export interface Named {
  email: string | null;
  name: string | null;
}

export function personLabel(person: Named, fallback = "Unknown"): string {
  return person.email ?? person.name ?? fallback;
}

/** True when we have no address for this person and should say so once. */
export function emailIsUnknown(person: Named): boolean {
  return person.email === null;
}
