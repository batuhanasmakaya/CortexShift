"""Unit tests for Session and LaunchSpecification domain models."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from cortexshift.domain.identifiers import generate_id, utc_now
from cortexshift.domain.launch import LaunchSpecification
from cortexshift.domain.provider import PROVIDER_CLAUDE
from cortexshift.domain.session import Session, SessionExitReason, SessionStatus


def test_session_creation_defaults() -> None:
    """Verify session defaults."""
    task_id = generate_id("task")
    session = Session(task_id=task_id, provider_id=PROVIDER_CLAUDE)

    assert session.id.startswith("sess_")
    assert session.task_id == task_id
    assert session.provider_id == PROVIDER_CLAUDE
    assert session.native_session_id is None
    assert session.status == SessionStatus.INITIALIZING
    assert session.ended_at is None
    assert session.exit_reason is None
    assert session.exit_code is None
    assert session.metadata == {}


def test_session_lifecycle_states() -> None:
    """Verify session status values and transitions."""
    task_id = generate_id("task")
    session = Session(
        task_id=task_id,
        provider_id=PROVIDER_CLAUDE,
        status=SessionStatus.RUNNING,
    )
    assert session.status == SessionStatus.RUNNING

    now = utc_now()
    completed = session.model_copy(
        update={
            "status": SessionStatus.COMPLETED,
            "ended_at": now,
            "exit_code": 0,
            "exit_reason": SessionExitReason.NORMAL_COMPLETION,
        }
    )
    assert completed.status == SessionStatus.COMPLETED
    assert completed.exit_code == 0
    assert completed.exit_reason == SessionExitReason.NORMAL_COMPLETION
    assert completed.ended_at == now

    interrupted = session.model_copy(
        update={
            "status": SessionStatus.INTERRUPTED,
            "ended_at": now,
            "exit_code": 130,
            "exit_reason": SessionExitReason.USER_INTERRUPTED,
        }
    )
    assert interrupted.status == SessionStatus.INTERRUPTED
    assert interrupted.exit_code == 130

    failed = session.model_copy(
        update={
            "status": SessionStatus.FAILED,
            "ended_at": now,
            "exit_code": 1,
            "exit_reason": SessionExitReason.PROCESS_CRASHED,
        }
    )
    assert failed.status == SessionStatus.FAILED
    assert failed.exit_code == 1

    spawn_failed = session.model_copy(
        update={
            "status": SessionStatus.FAILED,
            "ended_at": now,
            "exit_reason": SessionExitReason.SPAWN_FAILED,
        }
    )
    assert spawn_failed.exit_reason == SessionExitReason.SPAWN_FAILED


def test_session_immutability() -> None:
    """Verify session model is frozen."""
    session = Session(task_id="task_1", provider_id=PROVIDER_CLAUDE)
    with pytest.raises(ValidationError):
        session.status = SessionStatus.COMPLETED


def test_launch_specification_redaction() -> None:
    """Verify prompt redaction in LaunchSpecification."""
    spec_with_prompt = LaunchSpecification(
        provider_id=PROVIDER_CLAUDE,
        executable="/usr/local/bin/claude",
        cwd=Path("/path/to/project"),
        argv=["/usr/local/bin/claude", "Secret prompt with API keys"],
        interactive=True,
        initial_prompt_supported=True,
        prompt_supplied=True,
    )

    redacted_argv = spec_with_prompt.to_redacted_argv()
    assert redacted_argv == ["/usr/local/bin/claude", "<prompt>"]
    assert "Secret prompt with API keys" not in redacted_argv

    redacted_dict = spec_with_prompt.to_redacted_dict()
    assert redacted_dict["argv"] == ["/usr/local/bin/claude", "<prompt>"]
    assert redacted_dict["prompt_supplied"] is True

    # Without prompt
    spec_no_prompt = LaunchSpecification(
        provider_id=PROVIDER_CLAUDE,
        executable="/usr/local/bin/claude",
        cwd=Path("/path/to/project"),
        argv=["/usr/local/bin/claude"],
        interactive=True,
        initial_prompt_supported=True,
        prompt_supplied=False,
    )
    assert spec_no_prompt.to_redacted_argv() == ["/usr/local/bin/claude"]
    assert spec_no_prompt.to_redacted_dict()["prompt_supplied"] is False
