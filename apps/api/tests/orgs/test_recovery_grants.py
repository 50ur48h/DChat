"""A way back into an organization whose Admins can no longer sign in (**B-017**).

Over real HTTP against a real database, like the flow suite next door, because
the claim crosses the one seam that a fake would hide: it is looked up through
the *system* session, since the claimant is not an Admin and may not be a member
at all, and everything after the lookup happens org-scoped where RLS applies.

The test that carries the work package is
`test_a_locked_out_organization_gets_itself_back`. It does not assert that a
route returns 200 — it reproduces the state B-017 was filed from. Every Admin is
demoted, so nobody who can sign in can invite, promote or register anything, and
the only thing left is the grant that was armed while somebody still could.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import create_async_engine

from conftest import Api, audit_rows


async def _org_with_admin(api: Api, who: str = "alice") -> uuid.UUID:
    status, org = await api.call("POST", "/v1/orgs", who=who, body={"name": "Acme"})
    assert status == 201, org
    return uuid.UUID(org["org_id"])


async def _arm(api: Api, org_id: uuid.UUID, who: str = "alice", **body: Any) -> dict[str, Any]:
    status, grant = await api.call(
        "POST", f"/v1/orgs/{org_id}/recovery-grants", who=who, body=body or {}
    )
    assert status == 201, grant
    assert isinstance(grant, dict)
    return dict(grant)  # pyright: ignore[reportUnknownArgumentType]


async def _join(api: Api, org_id: uuid.UUID, *, who: str, role: str) -> None:
    """Put someone in the organization at a role, through the product."""
    status, invitation = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/invitations",
        who="alice",
        body={"email": f"{who}@example.com", "role": role},
    )
    assert status == 201, invitation
    status, _ = await api.call(
        "POST", "/v1/invitations/accept", who=who, body={"token": invitation["token"]}
    )
    assert status == 200


async def _demote_every_admin(url: URL, org_id: uuid.UUID) -> None:
    """Reproduce B-017 exactly: the memberships are still there, the people
    behind them cannot sign in.

    Done in the database on purpose. The product refuses to remove the last
    Admin — correctly — so the state this work package exists for is not
    reachable through the API, which is precisely why it needed a way out that
    is also not an ordinary API call.
    """
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)}
            )
            await connection.execute(
                text(
                    "UPDATE org_memberships SET role = 'reader' "
                    "WHERE org_id = :org AND role = 'admin'"
                ),
                {"org": org_id},
            )
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Arming
# ---------------------------------------------------------------------------


async def test_the_token_is_shown_once_and_never_again(api: Api) -> None:
    org_id = await _org_with_admin(api)

    armed = await _arm(api, org_id, label="Ops password manager")

    assert armed["token"], "the raw token comes back exactly once"
    assert armed["state"] == "armed"
    status, listed = await api.call("GET", f"/v1/orgs/{org_id}/recovery-grants", who="alice")
    assert status == 200
    assert listed[0]["label"] == "Ops password manager"
    assert "token" not in listed[0], "the list can never hand the credential back"


async def test_only_an_admin_can_arm_one(api: Api) -> None:
    """It mints a credential that makes its holder an Admin, so anyone who could
    arm one could promote themselves."""
    org_id = await _org_with_admin(api)
    await _join(api, org_id, who="bob", role="contributor")

    status, _ = await api.call("POST", f"/v1/orgs/{org_id}/recovery-grants", who="bob", body={})

    assert status == 403


async def test_arming_and_claiming_are_both_in_the_audit_log(api: Api, app_database: URL) -> None:
    """An organization whose Admin changed by this route has to be able to show
    when, by whom, and against which grant."""
    org_id = await _org_with_admin(api)
    armed = await _arm(api, org_id, label="Break glass")
    await _join(api, org_id, who="bob", role="reader")
    await api.call("POST", "/v1/recovery-grants/claim", who="bob", body={"token": armed["token"]})

    actions = [row.action for row in await audit_rows(app_database, org_id)]
    assert "org.recovery_armed" in actions
    assert "org.recovery_claimed" in actions


# ---------------------------------------------------------------------------
# Claiming
# ---------------------------------------------------------------------------


async def test_a_locked_out_organization_gets_itself_back(api: Api, app_database: URL) -> None:
    """**B-017, reproduced and then recovered.**

    Alice arms a grant while she is still Admin. Her identity then stops working
    — modelled here by demoting every Admin, which leaves the memberships intact
    and nobody able to use them. Bob, a Reader who *can* sign in, is now in the
    state the entry describes: he cannot invite, cannot promote, cannot register
    anything, and no Admin exists to ask.

    The grant is the only way out, and it is the whole point of the work package.
    """
    org_id = await _org_with_admin(api)
    armed = await _arm(api, org_id, label="Break glass")
    await _join(api, org_id, who="bob", role="reader")
    await _demote_every_admin(app_database, org_id)

    # The state B-017 was filed from: bricked, through the product's own eyes.
    status, _ = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/invitations",
        who="bob",
        body={"email": "carol@example.com", "role": "admin"},
    )
    assert status == 403, "nobody who can sign in can repair this organization"

    status, claimed = await api.call(
        "POST", "/v1/recovery-grants/claim", who="bob", body={"token": armed["token"]}
    )

    assert status == 200, claimed
    assert claimed["role"] == "admin"
    assert claimed["was_member"] is True, "he was already a Reader, and has been raised"
    # And the organization works again.
    status, _ = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/invitations",
        who="bob",
        body={"email": "carol@example.com", "role": "admin"},
    )
    assert status == 201


async def test_a_claim_raises_an_existing_membership(api: Api) -> None:
    """**The reason this is not an invitation.** `accept_invitation` adds a
    membership only where there is not one already, so a Reader redeeming an
    Admin invitation stays a Reader — and the locked-out person is usually
    already a member. This is the case that would silently fail."""
    org_id = await _org_with_admin(api)
    armed = await _arm(api, org_id)
    await _join(api, org_id, who="bob", role="reader")

    status, claimed = await api.call(
        "POST", "/v1/recovery-grants/claim", who="bob", body={"token": armed["token"]}
    )

    assert status == 200
    assert claimed["was_member"] is True
    status, me = await api.call("GET", "/v1/me", who="bob")
    assert [m["role"] for m in me["memberships"] if m["org_id"] == str(org_id)] == ["admin"]


async def test_a_claim_admits_someone_who_was_never_a_member(api: Api) -> None:
    org_id = await _org_with_admin(api)
    armed = await _arm(api, org_id)

    status, claimed = await api.call(
        "POST", "/v1/recovery-grants/claim", who="zoe", body={"token": armed["token"]}
    )

    assert status == 200
    assert claimed["was_member"] is False


async def test_a_grant_cannot_be_claimed_twice(api: Api) -> None:
    """Single use. A grant that stayed live after a recovery would be a permanent
    admin key sitting in somebody's notes."""
    org_id = await _org_with_admin(api)
    armed = await _arm(api, org_id)
    await api.call("POST", "/v1/recovery-grants/claim", who="bob", body={"token": armed["token"]})

    status, _ = await api.call(
        "POST", "/v1/recovery-grants/claim", who="carol", body={"token": armed["token"]}
    )

    assert status == 400


async def test_a_revoked_grant_cannot_be_claimed(api: Api) -> None:
    org_id = await _org_with_admin(api)
    armed = await _arm(api, org_id)

    status, revoked = await api.call(
        "POST", f"/v1/orgs/{org_id}/recovery-grants/{armed['id']}/revoke", who="alice", body={}
    )
    assert status == 200
    assert revoked["state"] == "revoked"

    status, _ = await api.call(
        "POST", "/v1/recovery-grants/claim", who="bob", body={"token": armed["token"]}
    )
    assert status == 400


async def test_every_bad_token_fails_with_the_same_words(api: Api) -> None:
    """Unknown, spent and revoked are deliberately indistinguishable, or this is
    an oracle for guessing tokens — the rule `accept_invitation` already follows."""
    org_id = await _org_with_admin(api)
    armed = await _arm(api, org_id)
    await api.call("POST", "/v1/recovery-grants/claim", who="bob", body={"token": armed["token"]})

    _, spent = await api.call(
        "POST", "/v1/recovery-grants/claim", who="carol", body={"token": armed["token"]}
    )
    _, unknown = await api.call(
        "POST", "/v1/recovery-grants/claim", who="carol", body={"token": "not-a-real-token"}
    )

    assert spent["detail"] == unknown["detail"]


async def test_a_grant_belongs_to_one_organization_only(api: Api) -> None:
    """A grant makes its holder an Admin of the organization that armed it, and
    of nothing else. The worst possible leak in this schema is the one where a
    token reaches across the tenant boundary."""
    acme = await _org_with_admin(api, who="alice")
    status, other = await api.call("POST", "/v1/orgs", who="dave", body={"name": "Globex"})
    assert status == 201
    armed = await _arm(api, acme)

    status, claimed = await api.call(
        "POST", "/v1/recovery-grants/claim", who="bob", body={"token": armed["token"]}
    )

    assert status == 200
    assert claimed["org_id"] == str(acme)
    status, me = await api.call("GET", "/v1/me", who="bob")
    assert [m["org_id"] for m in me["memberships"]] == [str(acme)]
    assert other["org_id"] != str(acme)
