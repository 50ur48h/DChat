"""Signup → org → invite → accept, across two organizations.

The M2 flow, run over real HTTP against a real database. Two orgs exist
throughout so that every assertion about what someone can see is also an
assertion about what they cannot.

The harness — the app, the stub validator, the `api` fixture — is in
`conftest.py`, shared with **B-017**'s recovery-grant suite.
"""

from __future__ import annotations

import uuid

from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import create_async_engine

from conftest import Api, audit_rows, subject_validator

# ---------------------------------------------------------------------------


async def test_me_works_before_you_belong_to_anything(api: Api) -> None:
    """Bootstrap has to start somewhere: a first login with no organization."""
    status, body = await api.call("GET", "/v1/me", who="alice")

    assert status == 200
    assert body["subject"] == "alice"
    assert body["memberships"] == []


async def test_the_full_signup_invite_accept_flow(api: Api, app_database: URL) -> None:
    # 1. Alice signs up and creates an organization; she becomes its Admin.
    status, org = await api.call("POST", "/v1/orgs", who="alice", body={"name": "Acme"})
    assert status == 201
    assert org["role"] == "admin"
    org_id = uuid.UUID(org["org_id"])

    # 2. Bob exists but belongs to nothing, and cannot see Acme's members.
    _, bob_me = await api.call("GET", "/v1/me", who="bob")
    assert bob_me["memberships"] == []
    denied, _ = await api.call("GET", f"/v1/orgs/{org_id}/members", who="bob")
    assert denied == 403

    # 3. Alice invites Bob as a Reader.
    status, invitation = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/invitations",
        who="alice",
        body={"email": "bob@example.com", "role": "reader"},
    )
    assert status == 201
    token = invitation["token"]

    # 4. Bob redeems it and is now a Reader.
    status, accepted = await api.call(
        "POST", "/v1/invitations/accept", who="bob", body={"token": token}
    )
    assert status == 200
    assert accepted["role"] == "reader"
    assert accepted["org_name"] == "Acme"

    # 5. Which shows up everywhere it should.
    _, bob_me = await api.call("GET", "/v1/me", who="bob")
    assert [(m["org_name"], m["role"]) for m in bob_me["memberships"]] == [("Acme", "reader")]

    status, members = await api.call("GET", f"/v1/orgs/{org_id}/members", who="bob")
    assert status == 200
    assert sorted(m["role"] for m in members) == ["admin", "reader"]

    # 6. And the whole story is in the organization's audit log.
    actions = [row[0] for row in await audit_rows(app_database, org_id)]
    assert actions == ["org.created", "invitation.created", "invitation.accepted"]


async def test_a_reader_cannot_do_admin_things(api: Api) -> None:
    """The M2 acceptance criterion, at the route level."""
    _, org = await api.call("POST", "/v1/orgs", who="alice", body={"name": "Acme"})
    org_id = uuid.UUID(org["org_id"])
    _, invitation = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/invitations",
        who="alice",
        body={"email": "bob@example.com", "role": "reader"},
    )
    await api.call("POST", "/v1/invitations/accept", who="bob", body={"token": invitation["token"]})

    status, _ = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/invitations",
        who="bob",
        body={"email": "carol@example.com", "role": "reader"},
    )

    assert status == 403


async def test_an_invitation_cannot_be_redeemed_twice(api: Api) -> None:
    _, org = await api.call("POST", "/v1/orgs", who="alice", body={"name": "Acme"})
    org_id = uuid.UUID(org["org_id"])
    _, invitation = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/invitations",
        who="alice",
        body={"email": "bob@example.com", "role": "reader"},
    )

    first, _ = await api.call(
        "POST", "/v1/invitations/accept", who="bob", body={"token": invitation["token"]}
    )
    second, body = await api.call(
        "POST", "/v1/invitations/accept", who="carol", body={"token": invitation["token"]}
    )

    assert first == 200
    assert second == 400
    assert body["detail"] == "That invitation is not valid. Ask an admin for a new one."


async def test_an_unknown_token_fails_identically_to_a_used_one(api: Api) -> None:
    """Same message, or this becomes an oracle for guessing tokens."""
    status, body = await api.call(
        "POST", "/v1/invitations/accept", who="bob", body={"token": "not-a-real-token"}
    )

    assert status == 400
    assert body["detail"] == "That invitation is not valid. Ask an admin for a new one."


async def test_the_raw_token_is_never_stored(api: Api, migrated_database: URL) -> None:
    """Only the hash is kept, so a leaked backup hands out no invitations."""
    _, org = await api.call("POST", "/v1/orgs", who="alice", body={"name": "Acme"})
    org_id = uuid.UUID(org["org_id"])
    _, invitation = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/invitations",
        who="alice",
        body={"email": "bob@example.com", "role": "reader"},
    )
    token = invitation["token"]

    engine = create_async_engine(migrated_database)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)}
            )
            stored = (
                (await connection.execute(text("SELECT token_hash FROM invitations")))
                .scalars()
                .all()
            )
            audited = (
                (await connection.execute(text("SELECT details::text FROM audit_log")))
                .scalars()
                .all()
            )
    finally:
        await engine.dispose()

    assert token not in stored
    assert all(token not in row for row in audited), "the raw token reached the audit log"


# ---------------------------------------------------------------------------
# A token that carried no email claim (B-009)
# ---------------------------------------------------------------------------


async def test_a_missing_email_claim_is_recorded_as_missing(
    api: Api, migrated_database: URL
) -> None:
    """Not as ``<subject>@unknown.invalid``, which is a lie a column would keep."""
    subject_validator(api).without_email.add("erin")

    status, me = await api.call("GET", "/v1/me", who="erin")

    assert status == 200
    assert me["email"] is None
    assert me["subject"] == "erin"

    engine = create_async_engine(migrated_database)
    try:
        async with engine.begin() as connection:
            stored = (
                await connection.execute(
                    text("SELECT email FROM users WHERE external_subject = 'erin'")
                )
            ).scalar_one()
    finally:
        await engine.dispose()

    assert stored is None
    assert "unknown.invalid" not in str(stored)


async def test_a_claim_that_arrives_later_is_recorded_then(api: Api) -> None:
    """The usual repair: an administrator adds the optional claim afterwards."""
    validator = subject_validator(api)
    validator.without_email.add("frank")
    await api.call("GET", "/v1/me", who="frank")

    validator.without_email.discard("frank")
    _, me = await api.call("GET", "/v1/me", who="frank")

    assert me["email"] == "frank@example.com"
    assert me["name"] == "Frank"


async def test_a_member_without_an_email_still_appears_in_the_list(api: Api) -> None:
    """A missing claim must not make somebody invisible to their own admins."""
    subject_validator(api).without_email.add("gwen")
    _, org = await api.call("POST", "/v1/orgs", who="alice", body={"name": "Acme"})
    org_id = uuid.UUID(org["org_id"])
    _, invitation = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/invitations",
        who="alice",
        body={"email": "gwen@example.com", "role": "reader"},
    )
    token = invitation["token"]
    await api.call("POST", "/v1/invitations/accept", who="gwen", body={"token": token})

    status, members = await api.call("GET", f"/v1/orgs/{org_id}/members", who="alice")

    assert status == 200
    assert {member["role"]: member["email"] for member in members} == {
        "admin": "alice@example.com",
        "reader": None,
    }


# ---------------------------------------------------------------------------
# Last-admin protection
# ---------------------------------------------------------------------------


async def test_the_only_admin_cannot_demote_themselves(api: Api) -> None:
    """An organization with no Admin cannot be repaired from inside the product."""
    _, org = await api.call("POST", "/v1/orgs", who="alice", body={"name": "Acme"})
    org_id = uuid.UUID(org["org_id"])
    _, me = await api.call("GET", "/v1/me", who="alice")

    status, body = await api.call(
        "PATCH",
        f"/v1/orgs/{org_id}/members/{me['user_id']}",
        who="alice",
        body={"role": "reader"},
    )

    assert status == 409
    assert "only Admin" in body["detail"]


async def test_the_only_admin_cannot_be_removed(api: Api) -> None:
    _, org = await api.call("POST", "/v1/orgs", who="alice", body={"name": "Acme"})
    org_id = uuid.UUID(org["org_id"])
    _, me = await api.call("GET", "/v1/me", who="alice")

    status, _ = await api.call("DELETE", f"/v1/orgs/{org_id}/members/{me['user_id']}", who="alice")

    assert status == 409


async def test_an_admin_may_step_down_once_someone_else_is_admin(api: Api) -> None:
    _, org = await api.call("POST", "/v1/orgs", who="alice", body={"name": "Acme"})
    org_id = uuid.UUID(org["org_id"])
    _, invitation = await api.call(
        "POST",
        f"/v1/orgs/{org_id}/invitations",
        who="alice",
        body={"email": "bob@example.com", "role": "admin"},
    )
    await api.call("POST", "/v1/invitations/accept", who="bob", body={"token": invitation["token"]})
    _, alice = await api.call("GET", "/v1/me", who="alice")

    status, member = await api.call(
        "PATCH",
        f"/v1/orgs/{org_id}/members/{alice['user_id']}",
        who="alice",
        body={"role": "reader"},
    )

    assert status == 200
    assert member["role"] == "reader"


# ---------------------------------------------------------------------------
# Two organizations
# ---------------------------------------------------------------------------


async def test_an_invitation_only_admits_you_to_its_own_organization(api: Api) -> None:
    _, acme = await api.call("POST", "/v1/orgs", who="alice", body={"name": "Acme"})
    _, globex = await api.call("POST", "/v1/orgs", who="dave", body={"name": "Globex"})
    acme_id, globex_id = uuid.UUID(acme["org_id"]), uuid.UUID(globex["org_id"])

    _, invitation = await api.call(
        "POST",
        f"/v1/orgs/{acme_id}/invitations",
        who="alice",
        body={"email": "bob@example.com", "role": "reader"},
    )
    await api.call("POST", "/v1/invitations/accept", who="bob", body={"token": invitation["token"]})

    inside, _ = await api.call("GET", f"/v1/orgs/{acme_id}/members", who="bob")
    outside, _ = await api.call("GET", f"/v1/orgs/{globex_id}/members", who="bob")

    assert inside == 200
    assert outside == 403


async def test_each_orgs_audit_log_holds_only_its_own_events(api: Api, app_database: URL) -> None:
    _, acme = await api.call("POST", "/v1/orgs", who="alice", body={"name": "Acme"})
    _, globex = await api.call("POST", "/v1/orgs", who="dave", body={"name": "Globex"})

    acme_rows = await audit_rows(app_database, uuid.UUID(acme["org_id"]))
    globex_rows = await audit_rows(app_database, uuid.UUID(globex["org_id"]))

    assert [row[0] for row in acme_rows] == ["org.created"]
    assert [row[0] for row in globex_rows] == ["org.created"]
    assert acme_rows[0][2]["name"] == "Acme"
    assert globex_rows[0][2]["name"] == "Globex"
