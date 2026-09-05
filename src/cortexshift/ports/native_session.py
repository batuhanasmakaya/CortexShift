"""Optional exact-resume boundary implemented only by capable runtime adapters."""

from pathlib import Path
from typing import Protocol, runtime_checkable

from cortexshift.domain.launch import LaunchSpecification
from cortexshift.domain.native_session import NativeSessionCapabilities


@runtime_checkable
class ProviderNativeSessionAdapter(Protocol):
    def get_native_capabilities(self) -> NativeSessionCapabilities:
        """Describe reliable identity and resume mechanisms."""
        ...

    def build_exact_resume(
        self, project_root: Path, executable_path: str, native_session_id: str
    ) -> LaunchSpecification:
        """Build an exact native resume, without a model turn or process launch."""
        ...
