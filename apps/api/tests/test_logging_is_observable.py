"""The application's loggers are still switched on after the session migrates.

**Why this file exists** (**B-127**). `alembic/env.py` calls `fileConfig(...)`,
whose `disable_existing_loggers` argument defaults to **True**. Every
`dataagent.*` logger is created at import; the test session migrates once at
start; so from that moment every application logger in the process had
`disabled = True` and produced nothing at all.

**The asymmetry is what makes it a check that cannot fail.** An assertion that a
line *is* logged fails loudly — that is how this was found, when B-126's first
`caplog` assertion captured an empty string. An assertion that *nothing* was
logged passes vacuously, and "nothing was logged" is precisely the shape a
control case takes. So the direction that stayed green is the one written to
prove the other assertions are not vacuous.

**Scope, stated rather than implied.** This was never a production outage.
Nothing under `src/dataagent` runs Alembic in-process — the deployed migration
job is a separate container command — so the deployed API's own logs were always
real. What was disabled was the ability to *test* logging, in a repository whose
characteristic defect is capability that is built, tested and unreachable.
"""

from __future__ import annotations

import logging
from types import ModuleType

import pytest
from sqlalchemy import URL

from dataagent.agent import runner, scheduler
from dataagent.auth import audit, jwt_validator
from dataagent.db import security_events

#: The modules whose log line is the record of last resort — each writes
#: somewhere durable first and logs only when that write itself failed, so if
#: these are silent there is no record at all. Listed rather than discovered, so
#: that removing a logger is a visible edit here.
LAST_RESORT: tuple[ModuleType, ...] = (audit, security_events, jwt_validator, scheduler, runner)


def test_no_application_logger_is_disabled(migrated_database: URL) -> None:
    """`migrated_database` is requested for its side effect.

    It is the fixture that runs the migrations, and the migrations are what used
    to do the damage — a version of this test without it passes against the
    defect, which would make it one more check that cannot fail.
    """
    assert migrated_database is not None

    disabled = sorted(
        name
        for name, entry in logging.Logger.manager.loggerDict.items()
        if name.startswith("dataagent") and isinstance(entry, logging.Logger) and entry.disabled
    )

    assert not disabled, (
        f"{disabled} are disabled, so nothing they log can be observed and every "
        "assertion that they logged nothing is vacuous. The usual cause is a "
        "`logging.config.fileConfig`/`dictConfig` call with the default "
        "`disable_existing_loggers=True` — alembic/env.py is the one this "
        "repository already had (B-127)."
    )


@pytest.mark.parametrize("module", LAST_RESORT, ids=lambda m: m.__name__)
def test_each_last_resort_logger_actually_emits(
    module: ModuleType, migrated_database: URL, caplog: pytest.LogCaptureFixture
) -> None:
    """The other direction, and the one the control cases needed.

    "No logger is disabled" is a claim about a flag. This is the claim that
    matters — a record reaches a handler — made about the modules where the log
    line is the only remaining evidence that something went wrong.
    """
    assert migrated_database is not None
    name = str(module.logger.name)  # pyright: ignore[reportAttributeAccessIssue]

    with caplog.at_level(logging.WARNING, logger=name):
        logging.getLogger(name).warning("probe from %s", module.__name__)

    assert caplog.records, (
        f"{name} emitted nothing. Its log line is the record kept when the "
        "durable write has already failed, so a silent logger there means a "
        "failure with no record anywhere."
    )
