"""Shared pytest configuration.

Keeps the suite hermetic against ambient developer state, so a run behaves the
same on a contributor's laptop as on a clean CI runner.

Two things leak in otherwise:

``git`` identity
    Several tests shell out to ``git commit``, which resolves the author
    identity from system and user config. Without an explicit identity those
    tests depend on whatever the developer happens to have configured, and fail
    on a clean runner with ``Author identity unknown`` (exit 128).

Builder agent CLI
    Builder resolves an external CLI from the environment, defaulting to
    ``cursor`` on ``PATH`` (see :mod:`argus.builder.invoke`). On a machine with
    the Cursor CLI installed, tests that do not pin the executable will invoke
    the real agent as a subprocess. Pointing the lookup at a path that cannot
    exist keeps the suite from reaching a real agent. Tests that exercise
    invocation set these variables themselves and still override this default.
"""

from __future__ import annotations

from typing import Iterator

import pytest

GIT_TEST_NAME = "Argus Test"
GIT_TEST_EMAIL = "tests@argus.invalid"

# Deliberately non-existent: resolution must fail rather than find an installed agent.
UNRESOLVABLE_CLI = "/nonexistent/argus-tests-no-agent-cli"


@pytest.fixture(scope="session", autouse=True)
def hermetic_test_environment(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[None]:
    config = tmp_path_factory.mktemp("git-config") / "gitconfig"
    config.write_text(
        "[user]\n"
        f"\tname = {GIT_TEST_NAME}\n"
        f"\temail = {GIT_TEST_EMAIL}\n"
        "[init]\n"
        "\tdefaultBranch = master\n"
        "[commit]\n"
        "\tgpgsign = false\n",
        encoding="utf-8",
    )

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("GIT_CONFIG_NOSYSTEM", "1")
        mp.setenv("GIT_CONFIG_GLOBAL", str(config))
        mp.setenv("GIT_AUTHOR_NAME", GIT_TEST_NAME)
        mp.setenv("GIT_AUTHOR_EMAIL", GIT_TEST_EMAIL)
        mp.setenv("GIT_COMMITTER_NAME", GIT_TEST_NAME)
        mp.setenv("GIT_COMMITTER_EMAIL", GIT_TEST_EMAIL)
        mp.setenv("GIT_TERMINAL_PROMPT", "0")
        mp.setenv("ARGUS_CURSOR_CLI", UNRESOLVABLE_CLI)
        mp.setenv("CURSOR_CLI", UNRESOLVABLE_CLI)
        mp.setenv("ARGUS_AGENT_CLI", UNRESOLVABLE_CLI)
        yield
