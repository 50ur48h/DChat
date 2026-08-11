"""Who the caller is, according to the identity provider.

A ``Principal`` answers only "who is this". It carries no organization and no
role: those are ours, not the IdP's, because membership is org-scoped,
invitation-driven and product-owned (architecture Part 6.1). Resolving a
principal into an organization and a role is WP2.1b's job.
"""

from __future__ import annotations

from dataclasses import dataclass


class TokenError(Exception):
    """A bearer token that cannot be trusted.

    ``code`` is a short machine-readable reason. The *message* is safe to log;
    neither is ever specific enough to help an attacker distinguish "no such
    user" from "wrong signature" in an API response — routes answer 401 and
    nothing more.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    email: str | None = None
    name: str | None = None
