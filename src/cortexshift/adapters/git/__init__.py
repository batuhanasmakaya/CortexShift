"""Git adapter package for repository inspection."""

from cortexshift.adapters.git.inspector import GitRepositoryInspector
from cortexshift.adapters.git.parser import ParsedGitStatus, parse_porcelain_status

__all__ = [
    "GitRepositoryInspector",
    "ParsedGitStatus",
    "parse_porcelain_status",
]
