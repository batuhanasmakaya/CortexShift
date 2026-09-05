"""Integration tests for process restarts, subdirectory discovery, and project isolation."""

import json
import subprocess
import sys
from pathlib import Path

from cortexshift.adapters.sqlite.store import SQLiteStateStore


def test_process_restart_persistence(tmp_path: Path) -> None:
    """Verify that tasks, active task pointer, and progress survive separate process invocations."""
    project_dir = tmp_path / "durable_app"
    project_dir.mkdir()

    # Process 1: Initialize project
    init_res = subprocess.run(
        [sys.executable, "-m", "cortexshift", "init", str(project_dir), "--name", "RestartTest"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert init_res.returncode == 0

    # Process 2: Start a task
    start_res = subprocess.run(
        [
            sys.executable,
            "-m",
            "cortexshift",
            "task",
            "start",
            "-t",
            "Survive Restart",
            "-o",
            "Verify durability across processes",
        ],
        cwd=project_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    assert start_res.returncode == 0

    # Process 3: Update task progress
    upd_res = subprocess.run(
        [
            sys.executable,
            "-m",
            "cortexshift",
            "task",
            "update",
            "--current-work",
            "Persisting to disk",
            "--add-completed",
            "Item 1",
            "--add-remaining",
            "Item 2",
            "--add-issue",
            "Issue 1",
        ],
        cwd=project_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    assert upd_res.returncode == 0

    # Process 4: Fresh process reads status via --json
    status_res = subprocess.run(
        [sys.executable, "-m", "cortexshift", "status", "--json"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    assert status_res.returncode == 0
    status_data = json.loads(status_res.stdout)

    assert status_data["name"] == "RestartTest"
    assert status_data["schema_version"] == 1
    assert status_data["active_task"] is not None
    assert status_data["active_task"]["title"] == "Survive Restart"
    assert status_data["active_task"]["status"] == "in_progress"
    assert status_data["active_task"]["progress"]["completed"] == 1
    assert status_data["active_task"]["progress"]["remaining"] == 1
    assert status_data["active_task"]["progress"]["issues"] == 1

    # Process 5: Fresh process reads task show via --json
    show_res = subprocess.run(
        [sys.executable, "-m", "cortexshift", "task", "show", "--json"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    assert show_res.returncode == 0
    show_data = json.loads(show_res.stdout)
    assert show_data["current_work"] == "Persisting to disk"
    assert show_data["completed_items"] == ["Item 1"]
    assert show_data["remaining_items"] == ["Item 2"]
    assert show_data["known_issues"] == ["Issue 1"]


def test_subdirectory_discovery_regression(tmp_path: Path) -> None:
    """Verify that commands run from deeply nested subdirectories find the root project."""
    project_dir = tmp_path / "deep_repo"
    project_dir.mkdir()

    # Initialize at root
    init_res = subprocess.run(
        [sys.executable, "-m", "cortexshift", "init", str(project_dir), "--name", "DeepProject"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert init_res.returncode == 0

    # Create a task at root
    subprocess.run(
        [sys.executable, "-m", "cortexshift", "task", "start", "-t", "Subdir Task", "-o", "Obj"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        check=True,
    )

    # Make deep nested directories
    deep_child = project_dir / "src" / "cortexshift" / "adapters" / "sqlite"
    deep_child.mkdir(parents=True)

    # Query status from deep child
    status_res = subprocess.run(
        [sys.executable, "-m", "cortexshift", "status", "--json"],
        cwd=deep_child,
        capture_output=True,
        text=True,
        check=False,
    )
    assert status_res.returncode == 0
    data = json.loads(status_res.stdout)
    assert data["name"] == "DeepProject"
    assert data["active_task"]["title"] == "Subdir Task"


def test_project_state_isolation(tmp_path: Path) -> None:
    """Verify two independent projects do not leak state or active tasks."""
    proj_a = tmp_path / "project_a"
    proj_b = tmp_path / "project_b"
    proj_a.mkdir()
    proj_b.mkdir()

    # Initialize both
    subprocess.run(
        [sys.executable, "-m", "cortexshift", "init", str(proj_a), "--name", "ProjA"],
        check=True,
    )
    subprocess.run(
        [sys.executable, "-m", "cortexshift", "init", str(proj_b), "--name", "ProjB"],
        check=True,
    )

    # Start task in A
    subprocess.run(
        [
            sys.executable,
            "-m",
            "cortexshift",
            "task",
            "start",
            "-t",
            "Task Only In A",
            "-o",
            "Obj A",
        ],
        cwd=proj_a,
        check=True,
    )

    # Start task in B
    subprocess.run(
        [
            sys.executable,
            "-m",
            "cortexshift",
            "task",
            "start",
            "-t",
            "Task Only In B",
            "-o",
            "Obj B",
        ],
        cwd=proj_b,
        check=True,
    )

    # Check list in A
    list_a_res = subprocess.run(
        [sys.executable, "-m", "cortexshift", "task", "list", "--json"],
        cwd=proj_a,
        capture_output=True,
        text=True,
        check=True,
    )
    tasks_a = json.loads(list_a_res.stdout)
    assert len(tasks_a) == 1
    assert tasks_a[0]["title"] == "Task Only In A"

    # Check list in B
    list_b_res = subprocess.run(
        [sys.executable, "-m", "cortexshift", "task", "list", "--json"],
        cwd=proj_b,
        capture_output=True,
        text=True,
        check=True,
    )
    tasks_b = json.loads(list_b_res.stdout)
    assert len(tasks_b) == 1
    assert tasks_b[0]["title"] == "Task Only In B"


def test_no_credential_or_secret_fields_in_schema(tmp_path: Path) -> None:
    """Verify database schema contains no fields designed for credentials, secrets, or tokens."""
    db_file = tmp_path / "inspection.sqlite3"
    with SQLiteStateStore(db_file) as store:
        cursor = store._conn.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall()]

        prohibited_terms = {
            "token",
            "secret",
            "password",
            "api_key",
            "credential",
            "auth",
            "transcript",
        }

        for table in tables:
            assert table not in prohibited_terms, f"Prohibited table found: {table}"
            cursor.execute(f"PRAGMA table_info({table});")
            columns = [col[1] for col in cursor.fetchall()]
            for col in columns:
                assert col.lower() not in prohibited_terms, f"Prohibited column {table}.{col} found"


def test_database_schema_inspection(tmp_path: Path) -> None:
    """Verify expected tables and foreign key configuration in actual SQLite database."""
    db_file = tmp_path / "schema_check.sqlite3"
    with SQLiteStateStore(db_file) as store:
        cursor = store._conn.cursor()

        # Check tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
        table_names = {row[0] for row in cursor.fetchall()}
        assert "schema_metadata" in table_names
        assert "projects" in table_names
        assert "tasks" in table_names
        assert "project_runtime" in table_names

        # Check foreign keys pragma
        cursor.execute("PRAGMA foreign_keys;")
        assert cursor.fetchone()[0] == 1


def test_canonical_context_restart_persistence_integration(tmp_path: Path) -> None:
    """Regression test: verify objective, requirements, constraints, and progress survive restart.

    1. create temporary initialized CortexShift project
    2. create task with distinctive objective: 'Preserve this exact objective'
    3. add at least two requirements: ['Requirement A', 'Requirement B']
    4. add at least two constraints: ['Constraint A', 'Constraint B']
    5. close/discard the store/service (process boundary)
    6. create a fresh store/application instance
    7. load the task
    8. verify exact semantic values survive
    9. update progress and verify across a second process boundary
    """
    project_dir = tmp_path / "canonical_repo"
    project_dir.mkdir()

    # Step 1: Initialize
    subprocess.run(
        [sys.executable, "-m", "cortexshift", "init", str(project_dir), "--name", "CanonicalProj"],
        capture_output=True,
        text=True,
        check=True,
    )

    # Step 2-4: Create task with objective, requirements, constraints via CLI
    subprocess.run(
        [
            sys.executable,
            "-m",
            "cortexshift",
            "task",
            "start",
            "--title",
            "Canonical Context Test",
            "--objective",
            "Preserve this exact objective",
            "--requirement",
            "Requirement A",
            "--requirement",
            "Requirement B",
            "--constraint",
            "Constraint A",
            "--constraint",
            "Constraint B",
        ],
        cwd=project_dir,
        capture_output=True,
        text=True,
        check=True,
    )

    # Step 5-8: Fresh process queries task show --json
    show_res = subprocess.run(
        [sys.executable, "-m", "cortexshift", "task", "show", "--json"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    task_data = json.loads(show_res.stdout)
    assert task_data["title"] == "Canonical Context Test"
    assert task_data["objective"] == "Preserve this exact objective"
    assert task_data["requirements"] == ["Requirement A", "Requirement B"]
    assert task_data["constraints"] == ["Constraint A", "Constraint B"]
    assert task_data["is_active"] is True
    assert task_data["status"] == "in_progress"

    # Step 9: Update progress via separate process
    subprocess.run(
        [
            sys.executable,
            "-m",
            "cortexshift",
            "task",
            "update",
            "--work",
            "In flight verification",
            "--add-completed",
            "Requirement A verified",
            "--add-remaining",
            "Requirement B pending",
            "--add-issue",
            "Known issue 1",
        ],
        cwd=project_dir,
        capture_output=True,
        text=True,
        check=True,
    )

    # Re-query in another fresh process
    show2_res = subprocess.run(
        [sys.executable, "-m", "cortexshift", "task", "show", "--json"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    task_data2 = json.loads(show2_res.stdout)
    assert task_data2["objective"] == "Preserve this exact objective"
    assert task_data2["requirements"] == ["Requirement A", "Requirement B"]
    assert task_data2["constraints"] == ["Constraint A", "Constraint B"]
    assert task_data2["current_work"] == "In flight verification"
    assert task_data2["completed_items"] == ["Requirement A verified"]
    assert task_data2["completed"] == ["Requirement A verified"]
    assert task_data2["remaining_items"] == ["Requirement B pending"]
    assert task_data2["remaining"] == ["Requirement B pending"]
    assert task_data2["known_issues"] == ["Known issue 1"]

    # Also verify via fresh in-process Python objects to test direct application/store layers
    from cortexshift.application.locator import ProjectLocator
    from cortexshift.application.task_service import TaskService

    db_path = ProjectLocator.get_database_path(project_dir)
    with SQLiteStateStore(db_path, auto_migrate=False) as fresh_store:
        fresh_service = TaskService(fresh_store)
        fresh_task = fresh_service.get_task(task_data["id"])
        assert fresh_task.objective == "Preserve this exact objective"
        assert fresh_task.requirements == ["Requirement A", "Requirement B"]
        assert fresh_task.constraints == ["Constraint A", "Constraint B"]
        assert fresh_task.completed == ["Requirement A verified"]
        assert fresh_task.remaining == ["Requirement B pending"]
        assert fresh_task.known_issues == ["Known issue 1"]
        assert fresh_task.current_work == "In flight verification"
