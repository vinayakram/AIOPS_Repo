from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from typing import Callable

from core.settings import settings


@dataclass
class CodexExecResult:
    command: list[str]
    return_code: int
    stdout: str
    stderr: str
    forced_stop: bool = False
    stop_reason: str = ""


class CodexCLI:
    def __init__(self) -> None:
        self.codex_command = settings.codex_command
        self.codex_model = getattr(settings, "codex_model", None)

    def exec(
        self,
        *,
        cwd: Path,
        prompt: str,
        sandbox: str,
        on_event: Callable[[str], None] | None = None,
        auto_stop_after_changes_seconds: int | None = None,
        hard_timeout_seconds: int | None = None,
        poll_interval_seconds: float = 1.0,
        _fallback_attempt: bool = False,
    ) -> CodexExecResult:
        if sandbox not in {"read-only", "workspace-write", "danger-full-access"}:
            raise ValueError(f"Invalid sandbox: {sandbox}")

        clean_prompt = (prompt or "").strip()
        if not clean_prompt:
            raise ValueError("Prompt is empty. Codex cannot run without a prompt.")

        cmd = [
            self.codex_command,
            "exec",
            "--cd",
            str(cwd),
            "--sandbox",
            sandbox,
            "--color",
            "never",
            "--skip-git-repo-check",
        ]

        if self.codex_model:
            cmd.extend(["--model", self.codex_model])

        cmd.append("-")

        if on_event:
            on_event(f"Starting Codex CLI in {sandbox} mode.")
            on_event(f"Command: {' '.join(cmd)}")

        completed = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            bufsize=1,
        )

        assert completed.stdin is not None
        completed.stdin.write(clean_prompt)
        completed.stdin.close()

        stdout_lines: list[str] = []
        line_queue: Queue[str | None] = Queue()

        def _reader() -> None:
            if completed.stdout is not None:
                for line in completed.stdout:
                    line_queue.put(line)
            line_queue.put(None)

        reader = Thread(target=_reader, daemon=True)
        reader.start()

        forced_stop = False
        stop_reason = ""
        reader_finished = False
        start_time = time.monotonic()

        while True:
            drained_any = False
            while True:
                try:
                    item = line_queue.get_nowait()
                except Empty:
                    break
                drained_any = True
                if item is None:
                    reader_finished = True
                    break
                stdout_lines.append(item)
                if on_event:
                    on_event(item.rstrip())

            if completed.poll() is not None and reader_finished:
                break

            elapsed = time.monotonic() - start_time
            if (
                not forced_stop
                and auto_stop_after_changes_seconds
                and elapsed >= auto_stop_after_changes_seconds
                and self._repo_has_changes(cwd)
            ):
                forced_stop = True
                stop_reason = "changes_detected"
                if on_event:
                    on_event(
                        "Detected repository changes after watchdog timeout. "
                        "Stopping Codex CLI and continuing with verification."
                    )
                completed.terminate()
                try:
                    completed.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    completed.kill()
                continue

            if not forced_stop and hard_timeout_seconds and elapsed >= hard_timeout_seconds:
                forced_stop = True
                stop_reason = "hard_timeout"
                if on_event:
                    on_event(
                        "Codex CLI exceeded the demo time budget. "
                        "Stopping the run and handing control back to remediation service."
                    )
                completed.terminate()
                try:
                    completed.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    completed.kill()
                continue

            if not drained_any:
                time.sleep(poll_interval_seconds)

        return_code = completed.wait()
        stdout = "".join(stdout_lines)

        if on_event:
            on_event(f"Codex CLI finished with return code {return_code}.")

        result = CodexExecResult(
            command=cmd,
            return_code=return_code,
            stdout=stdout,
            stderr="",
            forced_stop=forced_stop,
            stop_reason=stop_reason,
        )

        if (
            not _fallback_attempt
            and settings.codex_sandbox_fallback_enabled
            and sandbox != settings.codex_sandbox_fallback_mode
            and self._is_runtime_sandbox_failure(stdout)
        ):
            fallback_sandbox = settings.codex_sandbox_fallback_mode
            if fallback_sandbox not in {"read-only", "workspace-write", "danger-full-access"}:
                fallback_sandbox = "danger-full-access"
            if on_event:
                on_event(
                    "Detected local runtime sandbox failure. "
                    f"Retrying Codex CLI with {fallback_sandbox} sandbox."
                )
            return self.exec(
                cwd=cwd,
                prompt=prompt,
                sandbox=fallback_sandbox,
                on_event=on_event,
                auto_stop_after_changes_seconds=auto_stop_after_changes_seconds,
                hard_timeout_seconds=hard_timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
                _fallback_attempt=True,
            )

        return result

    @staticmethod
    def _is_runtime_sandbox_failure(output: str) -> bool:
        text = (output or "").lower()
        return (
            "bwrap: loopback: failed rtm_newaddr" in text
            or "bwrap: can't find source path" in text
            or "blocked by runtime sandbox" in text
            or "sandbox error" in text
            or "workspace path itself is inaccessible" in text
            or "cannot read the allowed repo path" in text
        )

    def _repo_has_changes(self, cwd: Path) -> bool:
        completed = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
        return bool(completed.stdout.strip())
