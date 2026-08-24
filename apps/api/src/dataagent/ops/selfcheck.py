"""Can this deployment's identity do what the product needs? (**B-125**, **B-129**)

**The failure this exists to prevent has already happened once.** `roles.bicep`
granted the app identity **Key Vault Secrets User** — read-only — with a comment
arguing convincingly for the narrow role: *"the application reads the OpenAI key
and has no business rotating it."* True of the configuration secrets, and it
quietly redefined the application as the thing that reads its own config rather
than the thing that stores **customers' database credentials**. Registering a data
source writes a secret, so the product's central action failed on a deployment
that was otherwise healthy, and the way we found out was a person clicking a
button and getting *"Could not reach the API."*

**Blob storage is in exactly that position now.** Every query execution writes a
result artifact and `chart.py` reads it back, so the deployed Blob path is
exercised for the first time by the first question somebody asks. Same shape,
different resource.

**Why this runs as a Container Apps job and not as a step in the pipeline.** The
smoke script authenticates as the **OIDC deploy identity**, which holds broad
permissions on the resource group. A vault write from the runner would have
passed happily throughout the entire period B-125 was live — a check that passes
for a reason unrelated to the thing it checks, which is the class this repository
keeps rediscovering. The only way to test what the *app* can do is to be the app,
so this runs on the same user-assigned identity the API runs on.

**And it goes through the product's own providers**, `get_secrets_provider()` and
`artifact_store()`, rather than calling the Azure SDK directly. A bespoke SDK call
can succeed against a resource the product cannot use — wrong container, wrong
name transformation, wrong credential chain — and would be one more thing that
works in isolation while the live path is broken.

**Backend-agnostic on purpose.** It checks whatever is configured, so CI runs it
against the local backends as a control — proving the check can pass at all — and
the deploy runs it against Key Vault and Blob. A check only ever seen to pass in
one environment is a check nobody has calibrated.

**What it deliberately does not do.** It never touches the database, so it can run
before or after the migration job and diagnoses nothing about Postgres. It writes
under identifiers that cannot collide with tenant data, and cleans up after
itself.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from typing import Final

from dataagent.config import Settings, get_settings
from dataagent.dal.artifacts import artifact_store
from dataagent.secrets.base import SecretNotFoundError
from dataagent.secrets.factory import get_secrets_provider

#: The namespace every probe write goes under, named for what it is rather than
#: for what it holds — there is no secret here, only a throwaway reference.
#:
#: Deliberately outside `ds/`, where real data-source credentials live: a probe
#: can never be mistaken for one, and a sweep over `ds/` can never delete a probe
#: mid-flight.
PROBE_NAMESPACE: Final = "selfcheck"

#: A probe payload with no resemblance to a credential. If this ever turns up in a
#: log or an exception, it says what it is.
PROBE_VALUE: Final = "deployment-selfcheck-probe"


class SelfCheckError(Exception):
    """A check failed, with a message that names the permission to grant."""


async def check_secrets(settings: Settings) -> str:
    """Write, read, delete, confirm gone — all four, because the product needs all four.

    **`delete` is not padding.** Rotating a data source's credential and removing
    a data source both delete, and `delete` is the verb *Secrets User* lacked
    alongside `set` when B-125 shipped. A check that only wrote would have gone
    green on a role that could not clean up after itself.
    """
    provider = get_secrets_provider()
    ref = f"{PROBE_NAMESPACE}/{uuid.uuid4()}"

    try:
        await provider.put(ref, {"probe": PROBE_VALUE})
    except Exception as error:
        raise SelfCheckError(
            f"could not WRITE a secret to {settings.secrets_backend}: {error}. "
            "For Key Vault the identity needs a role that can set a secret — "
            "'Key Vault Secrets Officer'. 'Key Vault Secrets User' can only read, "
            "which is B-125 exactly."
        ) from error

    try:
        got = await provider.get(ref)
    except Exception as error:
        raise SelfCheckError(f"wrote a secret and could not READ it back: {error}") from error

    if got.get("probe") != PROBE_VALUE:
        raise SelfCheckError(
            "the secret read back is not the one written, so the store is not "
            "round-tripping values. Nothing else here is trustworthy until that is."
        )

    try:
        await provider.delete(ref)
    except Exception as error:
        raise SelfCheckError(
            f"could not DELETE a secret: {error}. Rotating and removing a data "
            "source both delete, so this permission is not optional."
        ) from error

    # The delete is only proven by the absence that follows it. A backend that
    # accepted the call and kept the value would pass every assertion above.
    try:
        await provider.get(ref)
    except SecretNotFoundError:
        return f"secrets ({settings.secrets_backend}): write, read, delete, and gone"
    raise SelfCheckError(
        "delete returned success and the secret is still readable. The credential "
        "of a removed data source would outlive the data source."
    )


async def check_artifacts(settings: Settings) -> str:
    """Write and read a result artifact the way a query execution does.

    **Two verbs, not four, and the asymmetry is real rather than an oversight.**
    `ArtifactStore` has no `delete`: nothing in this product removes an artifact,
    which is **B-021** — retention is a date in a column that no sweep reads. So
    the round trip asserted here is the one the product actually performs. The
    probe blob is cleaned up below on a best-effort basis, and the storage
    account's `expire-artifacts` lifecycle rule is what removes it otherwise.
    """
    store = artifact_store(settings)
    org_id, execution_id = uuid.uuid4(), uuid.uuid4()
    payload = PROBE_VALUE.encode()

    # **`finally`, and the first version did not have it.** Closing after the last
    # assertion leaves the pools open on every path that raises — which is every
    # path this check exists for. The failure output then carried two
    # `Unclosed client session` lines under the sentence somebody needs to read,
    # in the one tool whose entire product is a diagnosis.
    try:
        try:
            reference = await store.put(org_id=org_id, execution_id=execution_id, payload=payload)
        except Exception as error:
            raise SelfCheckError(
                f"could not WRITE an artifact to {settings.artifacts_backend}: {error}. "
                "For Blob the identity needs 'Storage Blob Data Contributor' on the "
                "account, and the container must exist. Every query execution writes "
                "one of these, so nobody could ask a question."
            ) from error

        try:
            got = await store.get(org_id=org_id, reference=reference)
        except Exception as error:
            raise SelfCheckError(
                f"wrote an artifact and could not READ it back: {error}. An answer "
                "would be stored and its evidence unreadable."
            ) from error

        if got != payload:
            # `None` is the interesting case: `get` returns it for a missing blob,
            # so a write that silently went somewhere else looks exactly like this.
            state = "missing" if got is None else "not what was written"
            raise SelfCheckError(
                f"the artifact read back is {state}. A write that lands somewhere the "
                "read does not look produces this."
            )

        await _forget_probe_artifact(settings, reference)
        return f"artifacts ({settings.artifacts_backend}): write and read a result payload"
    finally:
        # `getattr` because only the Blob store has anything to close, and putting
        # `aclose` on the protocol would oblige every test double to grow a method
        # that does nothing.
        aclose = getattr(store, "aclose", None)
        if aclose is not None:
            await aclose()


async def _forget_probe_artifact(settings: Settings, reference: str) -> None:
    """Best effort, and its failure is deliberately not a check failure.

    The product does not delete artifacts (**B-021**), so a deployment that cannot
    delete one is not thereby broken — asserting on it would fail a deployment for
    a permission nothing needs. The lifecycle rule on the storage account removes
    whatever this leaves behind.
    """
    if settings.artifacts_backend != "blob":
        return
    try:
        from azure.identity.aio import DefaultAzureCredential
        from azure.storage.blob.aio import BlobServiceClient

        async with (
            DefaultAzureCredential() as credential,
            BlobServiceClient(
                account_url=settings.artifacts_account_url or "", credential=credential
            ) as service,
        ):
            blob = service.get_blob_client(container=settings.artifacts_container, blob=reference)
            await blob.delete_blob()
    except Exception as error:
        print(f"  note: the probe artifact was left behind ({error}).")
        print("  Harmless — the expire-artifacts lifecycle rule removes it.")


async def run() -> int:
    """Every check, then a verdict. Returns a process exit code."""
    settings = get_settings()
    print(f"selfcheck: identity probe against env={settings.env}")

    failures: list[str] = []
    for check in (check_secrets, check_artifacts):
        try:
            print(f"  ok  {await check(settings)}")
        except SelfCheckError as failure:
            print(f"  FAIL  {failure}", file=sys.stderr)
            failures.append(str(failure))

    if failures:
        print(
            f"selfcheck: {len(failures)} check(s) failed. The deployment is running "
            "and cannot do what the product needs — which is the state a person "
            "otherwise discovers by clicking a button.",
            file=sys.stderr,
        )
        return 1

    print("selfcheck: this identity can store a credential and store a result.")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
