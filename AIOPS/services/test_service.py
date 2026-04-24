from __future__ import annotations

import os
import shlex
import subprocess

from core.schemas import Issue, TestRunResult
from services.repo_service import validate_issue_paths


TEST_TIMEOUT_SECONDS = 10 * 10


def run_tests(issue: Issue) -> list[TestRunResult]:
    repo_root, _ = validate_issue_paths(issue)
    validation_source = issue.validation_command or issue.test_command
    commands = [cmd.strip() for cmd in validation_source.split("&&") if cmd.strip()]
    results: list[TestRunResult] = []

    if not commands:
        return [
            TestRunResult(
                command="manual-review-required",
                return_code=0,
                stdout="No automated validation command configured. Human validation is required before delivery.",
                stderr="",
            )
        ]

    for cmd in commands:
        try:
            args = shlex.split(cmd, posix=os.name != "nt")
            completed = subprocess.run(
                args,
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                shell=False,
                timeout=TEST_TIMEOUT_SECONDS,
            )
            result = TestRunResult(
                command=cmd,
                return_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
        except ValueError as exc:
            result = TestRunResult(
                command=cmd,
                return_code=2,
                stdout="",
                stderr=f"Validation command could not be parsed safely: {exc}",
            )
        except subprocess.TimeoutExpired as exc:
            result = TestRunResult(
                command=cmd,
                return_code=124,
                stdout=exc.stdout or "",
                stderr=(exc.stderr or "") + f"\nTest command timed out after {TEST_TIMEOUT_SECONDS} seconds.",
            )
        except FileNotFoundError as exc:
            result = TestRunResult(
                command=cmd,
                return_code=127,
                stdout="",
                stderr=(
                    f"Validation command is not available in this environment: {exc.filename}. "
                    "Install the tool or change the project's validation command."
                ),
            )

        results.append(result)

        if result.return_code != 0:
            break

    return results
