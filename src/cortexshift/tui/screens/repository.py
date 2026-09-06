"""The Repository section: live, strictly read-only Git working tree state.

This screen inspects; it never mutates. There is no staging, committing, checkout,
reset, or stash control anywhere in CortexShift's dashboard, and full diffs are never
rendered — only counts, file paths, and shortstat summaries.
"""

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Static

from cortexshift.tui.models import (
    DataAuthority,
    TuiRepositoryModel,
    format_timestamp,
)
from cortexshift.tui.screens import SectionView, TuiSection
from cortexshift.tui.widgets import BulletList, FieldList, Panel, marked


class RepositorySection(SectionView):
    """Renders the most recent live repository inspection."""

    section = TuiSection.REPOSITORY

    def compose(self) -> ComposeResult:
        """Build the repository layout."""
        yield Static(Text("Repository", style="bold"), classes="section-title")
        yield Static("", id="repo-heading")

        # Git facts span the full width so absolute paths and commit SHAs stay readable.
        with Panel("Git", authority=DataAuthority.LIVE):
            yield FieldList(id="repo-git")

        with Horizontal(classes="columns"):
            with Vertical(classes="column"):
                with Panel("Diff summary", authority=DataAuthority.LIVE):
                    yield FieldList(id="repo-diff")
                with Panel("Staged"):
                    yield BulletList(id="repo-staged")
                with Panel("Modified"):
                    yield BulletList(id="repo-modified")
            with Vertical(classes="column"):
                with Panel("Untracked"):
                    yield BulletList(id="repo-untracked")
                with Panel("Conflicted"):
                    yield BulletList(id="repo-conflicted")

        yield Static(
            Text(
                "Read-only view. CortexShift never stages, commits, checks out, resets, "
                "or stashes; full diffs are not displayed.",
                style="dim italic",
            ),
        )

    def update_repository(self, repository: TuiRepositoryModel | None) -> None:
        """Render an inspection result, or the in-flight state while one runs."""
        heading = self.query_one("#repo-heading", Static)

        if repository is None:
            heading.update(Text("Inspecting repository…", style="dim italic"))
            self.query_one("#repo-git", FieldList).set_fields([])
            self.query_one("#repo-diff", FieldList).set_fields([])
            for widget_id in (
                "#repo-staged",
                "#repo-modified",
                "#repo-untracked",
                "#repo-conflicted",
            ):
                self.query_one(widget_id, BulletList).set_items([], empty="—")
            return

        if not repository.ready:
            heading.update(marked("warn", f"Repository unavailable: {repository.status.value}"))
            self.query_one("#repo-git", FieldList).set_fields(
                [
                    ("Git available", "yes" if repository.git_available else "no"),
                    ("Project root", str(repository.project_root)),
                    ("Diagnostic", repository.diagnostic or "—"),
                ]
            )
            self.query_one("#repo-diff", FieldList).set_fields([])
            for widget_id in (
                "#repo-staged",
                "#repo-modified",
                "#repo-untracked",
                "#repo-conflicted",
            ):
                self.query_one(widget_id, BulletList).set_items([], empty="—")
            return

        branch = repository.branch or "(detached HEAD)"
        state = "dirty" if repository.dirty else "clean"
        summary = Text()
        summary.append(branch, style="bold")
        summary.append(" · ")
        summary.append_text(marked(state, state))
        summary.append(f" · {repository.changed_file_count} changed file(s)")
        heading.update(summary)

        git_root_relationship = "same as project root"
        if repository.git_root and repository.git_root != str(repository.project_root):
            git_root_relationship = repository.git_root

        self.query_one("#repo-git", FieldList).set_fields(
            [
                ("Git available", "yes" if repository.git_available else "no"),
                ("Git version", repository.git_version or "—"),
                ("Project root", str(repository.project_root)),
                ("Git root", git_root_relationship),
                ("Branch", branch),
                ("HEAD", repository.head_sha or "— (no commit yet)"),
                ("Detached HEAD", "yes" if repository.detached_head else "no"),
                ("Dirty", "yes" if repository.dirty else "no"),
                ("Observed at", format_timestamp(repository.observed_at)),
            ]
        )

        self.query_one("#repo-diff", FieldList).set_fields(
            [
                ("Staged", str(len(repository.staged_files))),
                ("Modified", str(len(repository.modified_files))),
                ("Untracked", str(len(repository.untracked_files))),
                ("Conflicted", str(len(repository.conflicted_files))),
                ("Working tree", repository.working_tree_diff_summary or "—"),
                ("Staged diff", repository.staged_diff_summary or "—"),
            ]
        )

        self.query_one("#repo-staged", BulletList).set_items(
            list(repository.staged_files), limit=10, empty="No staged changes."
        )
        self.query_one("#repo-modified", BulletList).set_items(
            list(repository.modified_files), limit=10, empty="No modified files."
        )
        self.query_one("#repo-untracked", BulletList).set_items(
            list(repository.untracked_files), limit=10, empty="No untracked files."
        )
        self.query_one("#repo-conflicted", BulletList).set_items(
            list(repository.conflicted_files), limit=10, empty="No conflicts."
        )
