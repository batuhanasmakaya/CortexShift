"""Native Git repository inspector adapter."""

import re
import shutil
from pathlib import Path

from cortexshift.adapters.command_runner import SubprocessCommandRunner
from cortexshift.adapters.git.parser import parse_porcelain_status
from cortexshift.domain.git import (
    GitSnapshot,
    RepositoryInspection,
    RepositoryInspectionStatus,
)
from cortexshift.domain.identifiers import generate_id, utc_now
from cortexshift.ports.command_runner import CommandRunner
from cortexshift.ports.repository import RepositoryInspector

_GIT_ENV: dict[str, str] = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_PAGER": "cat",
    "GIT_OPTIONAL_LOCKS": "0",
}

_GIT_VERSION_RE = re.compile(r"git\s+version\s+([0-9]+(?:\.[0-9]+)*)")


class GitRepositoryInspector(RepositoryInspector):
    """Concrete repository inspector using the native local git CLI.

    Invariants:
    - Never modifies repository state (strictly read-only commands).
    - Never uses shell=True.
    - Bound by finite timeouts.
    - Safe against NUL-delimited and unusual filenames.
    - Accurately scopes status and diff summaries in monorepos.
    - Distinguishes unborn repositories, detached HEADs, and non-git projects.
    """

    def __init__(
        self,
        command_runner: CommandRunner | None = None,
        default_timeout: float = 10.0,
    ) -> None:
        self._runner = command_runner or SubprocessCommandRunner(default_timeout=default_timeout)
        self.default_timeout = default_timeout

    def _extract_git_version(self) -> str | None:
        """Extract installed Git version string, or None if unavailable."""
        res = self._runner.run(["git", "--version"], timeout=5.0)
        if not res.success or not res.stdout:
            return None
        match = _GIT_VERSION_RE.search(res.stdout)
        if match:
            return match.group(1)
        return res.stdout.strip()

    def inspect(self, project_root: Path | str, project_id: str = "") -> RepositoryInspection:
        """Perform a safe, read-only live inspection of the Git repository for a project root."""
        resolved_root = Path(project_root).resolve()
        root_str = str(resolved_root)

        # 1. Verify git executable exists in PATH
        if shutil.which("git") is None:
            return RepositoryInspection(
                status=RepositoryInspectionStatus.GIT_NOT_INSTALLED,
                project_root=root_str,
                git_available=False,
                diagnostic="Git executable was not found in PATH.",
            )

        git_version = self._extract_git_version()

        # 2. Check if inside a Git working tree
        tree_check = self._runner.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=resolved_root,
            env=_GIT_ENV,
            timeout=self.default_timeout,
        )
        if tree_check.timed_out:
            return RepositoryInspection(
                status=RepositoryInspectionStatus.PROBE_ERROR,
                project_root=root_str,
                git_available=True,
                git_version=git_version,
                diagnostic="Git inspection timed out.",
            )
        if not tree_check.success or tree_check.stdout.strip() != "true":
            return RepositoryInspection(
                status=RepositoryInspectionStatus.NOT_GIT_REPOSITORY,
                project_root=root_str,
                git_available=True,
                git_version=git_version,
                diagnostic="This CortexShift project is not inside a Git repository.",
            )

        # 3. Detect Git repository root
        toplevel_res = self._runner.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=resolved_root,
            env=_GIT_ENV,
            timeout=self.default_timeout,
        )
        if toplevel_res.timed_out:
            return RepositoryInspection(
                status=RepositoryInspectionStatus.PROBE_ERROR,
                project_root=root_str,
                git_available=True,
                git_version=git_version,
                diagnostic="Git inspection timed out.",
            )
        if not toplevel_res.success or not toplevel_res.stdout.strip():
            return RepositoryInspection(
                status=RepositoryInspectionStatus.PROBE_ERROR,
                project_root=root_str,
                git_available=True,
                git_version=git_version,
                diagnostic="Git repository inspection failed.",
            )
        git_root = str(Path(toplevel_res.stdout.strip()).resolve())

        # 4. Detect prefix relative to git root (for monorepo scoping)
        prefix_res = self._runner.run(
            ["git", "rev-parse", "--show-prefix"],
            cwd=resolved_root,
            env=_GIT_ENV,
            timeout=self.default_timeout,
        )
        if prefix_res.timed_out:
            return RepositoryInspection(
                status=RepositoryInspectionStatus.PROBE_ERROR,
                project_root=root_str,
                git_available=True,
                git_version=git_version,
                diagnostic="Git inspection timed out.",
            )
        project_prefix = prefix_res.stdout.strip() if prefix_res.success else ""

        # 5. Detect branch & detached HEAD
        branch_res = self._runner.run(
            ["git", "branch", "--show-current"],
            cwd=resolved_root,
            env=_GIT_ENV,
            timeout=self.default_timeout,
        )
        if branch_res.timed_out:
            return RepositoryInspection(
                status=RepositoryInspectionStatus.PROBE_ERROR,
                project_root=root_str,
                git_available=True,
                git_version=git_version,
                diagnostic="Git inspection timed out.",
            )

        current_branch = branch_res.stdout.strip() if branch_res.success else ""
        branch: str | None = None
        detached_head = False

        if current_branch:
            branch = current_branch
            detached_head = False
        else:
            # Fallback for unborn branches or detached HEAD
            sym_res = self._runner.run(
                ["git", "symbolic-ref", "--short", "HEAD"],
                cwd=resolved_root,
                env=_GIT_ENV,
                timeout=self.default_timeout,
            )
            if sym_res.timed_out:
                return RepositoryInspection(
                    status=RepositoryInspectionStatus.PROBE_ERROR,
                    project_root=root_str,
                    git_available=True,
                    git_version=git_version,
                    diagnostic="Git inspection timed out.",
                )
            if sym_res.success and sym_res.stdout.strip():
                branch = sym_res.stdout.strip()
                detached_head = False
            else:
                branch = None
                # Check if HEAD commit exists
                head_verify = self._runner.run(
                    ["git", "rev-parse", "--verify", "HEAD"],
                    cwd=resolved_root,
                    env=_GIT_ENV,
                    timeout=self.default_timeout,
                )
                if head_verify.timed_out:
                    return RepositoryInspection(
                        status=RepositoryInspectionStatus.PROBE_ERROR,
                        project_root=root_str,
                        git_available=True,
                        git_version=git_version,
                        diagnostic="Git inspection timed out.",
                    )
                detached_head = head_verify.success

        # 6. Detect HEAD commit SHA
        head_sha: str | None = None
        head_res = self._runner.run(
            ["git", "rev-parse", "HEAD"],
            cwd=resolved_root,
            env=_GIT_ENV,
            timeout=self.default_timeout,
        )
        if head_res.timed_out:
            return RepositoryInspection(
                status=RepositoryInspectionStatus.PROBE_ERROR,
                project_root=root_str,
                git_available=True,
                git_version=git_version,
                diagnostic="Git inspection timed out.",
            )
        if head_res.success and head_res.stdout.strip():
            head_sha = head_res.stdout.strip()

        # 7. Status and changed files (NUL-delimited, scoped to project pathspec)
        status_res = self._runner.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", "."],
            cwd=resolved_root,
            env=_GIT_ENV,
            timeout=self.default_timeout,
            sanitize=False,
        )
        if status_res.timed_out:
            return RepositoryInspection(
                status=RepositoryInspectionStatus.PROBE_ERROR,
                project_root=root_str,
                git_available=True,
                git_version=git_version,
                diagnostic="Git inspection timed out.",
            )
        if not status_res.success:
            return RepositoryInspection(
                status=RepositoryInspectionStatus.PROBE_ERROR,
                project_root=root_str,
                git_available=True,
                git_version=git_version,
                diagnostic="Git repository inspection failed.",
            )

        parsed_status = parse_porcelain_status(status_res.stdout, project_prefix=project_prefix)

        # 8. Working tree diff shortstat
        diff_wt_res = self._runner.run(
            ["git", "diff", "--shortstat", "--", "."],
            cwd=resolved_root,
            env=_GIT_ENV,
            timeout=self.default_timeout,
        )
        if diff_wt_res.timed_out:
            return RepositoryInspection(
                status=RepositoryInspectionStatus.PROBE_ERROR,
                project_root=root_str,
                git_available=True,
                git_version=git_version,
                diagnostic="Git inspection timed out.",
            )
        wt_summary = (
            diff_wt_res.stdout.strip()
            if diff_wt_res.success and diff_wt_res.stdout.strip()
            else None
        )

        # 9. Staged diff shortstat
        diff_staged_res = self._runner.run(
            ["git", "diff", "--cached", "--shortstat", "--", "."],
            cwd=resolved_root,
            env=_GIT_ENV,
            timeout=self.default_timeout,
        )
        if diff_staged_res.timed_out:
            return RepositoryInspection(
                status=RepositoryInspectionStatus.PROBE_ERROR,
                project_root=root_str,
                git_available=True,
                git_version=git_version,
                diagnostic="Git inspection timed out.",
            )
        staged_summary = (
            diff_staged_res.stdout.strip()
            if diff_staged_res.success and diff_staged_res.stdout.strip()
            else None
        )

        dirty = bool(
            parsed_status.staged_files
            or parsed_status.modified_files
            or parsed_status.untracked_files
            or parsed_status.conflicted_files
        )

        snapshot = GitSnapshot(
            id=generate_id("snap"),
            project_id=project_id or "proj_default",
            project_root=root_str,
            git_root=git_root,
            git_version=git_version,
            branch=branch,
            head_sha=head_sha,
            detached_head=detached_head,
            dirty=dirty,
            staged_files=parsed_status.staged_files,
            modified_files=parsed_status.modified_files,
            untracked_files=parsed_status.untracked_files,
            conflicted_files=parsed_status.conflicted_files,
            working_tree_diff_summary=wt_summary,
            staged_diff_summary=staged_summary,
            captured_at=utc_now(),
            metadata={"renames": parsed_status.renames} if parsed_status.renames else {},
        )

        return RepositoryInspection(
            status=RepositoryInspectionStatus.READY,
            project_root=root_str,
            git_available=True,
            git_version=git_version,
            snapshot=snapshot,
        )
