"""Local high-confidence secret/path scan of release files and all reachable Git blobs.

Reports only path/category, never matched values. This is a heuristic preflight,
not proof that arbitrary credentials cannot exist. No network calls or uploads.
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    "private-key": rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    "provider-key": rb"\bsk-[A-Za-z0-9_-]{24,}",
    "github-token": rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})",
    "aws-key": rb"\bAKIA[A-Z0-9]{16}\b",
    "machine-home": (
        rb"(?:/Users/|/home/|C:\\Users\\)(?!developer\b|user\b|name\b|<)"
        rb"[A-Za-z0-9._-]+[/\\]"
    ),
}


def findings(data: bytes) -> list[str]:
    return [name for name, pattern in PATTERNS.items() if re.search(pattern, data)]


def main() -> int:
    paths = (
        subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT
        )
        .decode()
        .split("\0")
    )
    hits = []
    for name in sorted(set(paths)):
        path = ROOT / name
        if name and path.is_file():
            hits.extend((name, category) for category in findings(path.read_bytes()))
    blobs: dict[str, str] = {}
    commits = subprocess.check_output(["git", "rev-list", "--all"], cwd=ROOT).decode().splitlines()
    for commit in commits:
        for row in (
            subprocess.check_output(["git", "ls-tree", "-r", commit], cwd=ROOT)
            .decode()
            .splitlines()
        ):
            meta, path = row.split("\t", 1)
            blobs[meta.split()[2]] = path
    for oid, path in blobs.items():
        data = subprocess.check_output(["git", "cat-file", "blob", oid], cwd=ROOT)
        hits.extend((f"history:{path}", category) for category in findings(data))
    for path, category in sorted(set(hits)):
        print(f"{category}: {path}")
    print(
        f"Scanned release files and {len(blobs)} unique blobs across {len(commits)} commits; "
        f"{len(hits)} findings. Review generic credential-related fixtures separately."
    )
    return int(bool(hits))


if __name__ == "__main__":
    raise SystemExit(main())
