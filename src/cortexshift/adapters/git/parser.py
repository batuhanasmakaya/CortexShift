"""Parser for machine-readable Git porcelain status output."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ParsedGitStatus:
    """Structured collections of changed files extracted from Git status."""

    staged_files: list[str]
    modified_files: list[str]
    untracked_files: list[str]
    conflicted_files: list[str]
    renames: dict[str, str] = field(default_factory=dict)


def parse_porcelain_status(raw_output: str, project_prefix: str = "") -> ParsedGitStatus:
    """Parse NUL-delimited machine-readable output from `git status --porcelain=v1 -z`.

    Handles:
    - Unusual filenames containing spaces, tabs, newlines, and Unicode
    - Renames and copies consuming two consecutive NUL-delimited records
    - Scope normalization relative to project_prefix (monorepo support)
    - Conflict/unmerged status detection (UU, AA, DD, *U, U*)
    - Mixed index and working tree modifications (e.g., MM, AM, RM)
    - Deterministic ordering of all file collections

    Args:
        raw_output: NUL-separated stdout from `git status --porcelain=v1 -z`.
        project_prefix: Path prefix of the CortexShift project relative to git root.

    Returns:
        A populated ParsedGitStatus object with project-relative paths.
    """
    if not raw_output:
        return ParsedGitStatus(
            staged_files=[],
            modified_files=[],
            untracked_files=[],
            conflicted_files=[],
        )

    # Normalize prefix (strip leading/trailing slashes)
    clean_prefix = project_prefix.strip("/")
    prefix_with_slash = f"{clean_prefix}/" if clean_prefix else ""

    tokens = raw_output.split("\x00")
    if tokens and tokens[-1] == "":
        tokens.pop()

    staged: list[str] = []
    modified: list[str] = []
    untracked: list[str] = []
    conflicted: list[str] = []
    renames: dict[str, str] = {}

    i = 0
    while i < len(tokens):
        entry = tokens[i]
        if not entry:
            i += 1
            continue

        if len(entry) < 3:
            i += 1
            continue

        x = entry[0]
        y = entry[1]
        path = entry[3:]

        orig_path: str | None = None
        if x in ("R", "C") or y in ("R", "C"):
            i += 1
            if i < len(tokens):
                orig_path = tokens[i]

        i += 1

        # Check project scope: in monorepos, filter out files outside the project scope
        if clean_prefix:
            if not (path == clean_prefix or path.startswith(prefix_with_slash)):
                # Outside project scope
                continue
            # Normalize path relative to project root
            normalized_path = (
                path[len(prefix_with_slash) :] if path.startswith(prefix_with_slash) else "."
            )
        else:
            normalized_path = path

        # Normalize orig_path if present
        normalized_orig: str | None = None
        if orig_path is not None:
            if clean_prefix:
                if orig_path.startswith(prefix_with_slash):
                    normalized_orig = orig_path[len(prefix_with_slash) :]
                else:
                    normalized_orig = orig_path
            else:
                normalized_orig = orig_path

        if normalized_orig is not None:
            renames[normalized_path] = normalized_orig

        # Exclude CortexShift private runtime state directory
        if normalized_path == ".cortexshift" or normalized_path.startswith(".cortexshift/"):
            continue

        # 1. Untracked
        if x == "?" and y == "?":
            untracked.append(normalized_path)
            continue

        # 2. Ignored
        if x == "!" and y == "!":
            continue

        # 3. Unmerged / Conflicted
        # In Git porcelain, unmerged states: DD, AU, UD, UA, DU, AA, UU
        is_unmerged = "U" in (x, y) or (x == "A" and y == "A") or (x == "D" and y == "D")
        if is_unmerged:
            conflicted.append(normalized_path)
            continue

        # 4. Staged modifications (index changes)
        if x in ("M", "A", "D", "R", "C", "T"):
            staged.append(normalized_path)

        # 5. Unstaged modifications (working tree changes)
        if y in ("M", "D", "T"):
            modified.append(normalized_path)

    return ParsedGitStatus(
        staged_files=sorted(set(staged)),
        modified_files=sorted(set(modified)),
        untracked_files=sorted(set(untracked)),
        conflicted_files=sorted(set(conflicted)),
        renames=renames,
    )
