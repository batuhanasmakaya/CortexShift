"""Google Antigravity native CLI probe, runtime, and handoff delivery adapters."""

import json
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cortexshift.adapters.headless_runner import SubprocessHeadlessProviderRunner
from cortexshift.domain.doctor import AuthenticationStatus, ProviderDiagnostic
from cortexshift.domain.errors import (
    CortexShiftError,
    HandoffDeliveryError,
    NativeResumeError,
    UnsupportedPromptError,
)
from cortexshift.domain.handoff import HandoffFailureCode
from cortexshift.domain.launch import LaunchSpecification
from cortexshift.domain.native_session import NativeSessionCapabilities, valid_native_id
from cortexshift.domain.provider import PROVIDER_ANTIGRAVITY, ProviderCapabilities, ProviderId
from cortexshift.ports.command_runner import CommandRunner
from cortexshift.ports.discovery import ProviderProbe
from cortexshift.ports.handoff_delivery import (
    HandoffDeliveryPreparation,
    HandoffDeliveryStrategy,
    ProviderHandoffAdapter,
)
from cortexshift.ports.headless_runner import (
    DEFAULT_HEADLESS_TIMEOUT_SECONDS,
    HeadlessProviderRunner,
)
from cortexshift.ports.provider import ProviderRuntimeAdapter

_VERSION_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?(?:[-.][a-zA-Z0-9]+)?)")


def _extract_version(text: str) -> str | None:
    """Extract a clean semver-like version string from CLI output."""
    match = _VERSION_RE.search(text)
    if match:
        return match.group(1)
    first_line = text.splitlines()[0].strip() if text else ""
    return first_line[:32] if first_line and len(first_line) <= 32 else None


class AntigravityProviderProbe(ProviderProbe):
    """Probe for detecting and diagnosing the Google Antigravity native CLI (`agy`).

    CRITICAL INVARIANTS:
    - Never invoke `agy -p ...` or headless prompt commands to check authentication.
      Prompt execution consumes model quota and violates passive discovery.
    - Never read config or credential files from ~/.gemini/... or Keychain.
    - Since `agy` does not currently expose a documented passive, non-model auth status
      command, authentication MUST be reported as UNKNOWN.
    """

    def __init__(
        self,
        command_runner: CommandRunner,
        which_fn: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self._runner = command_runner
        self._which = which_fn

    @property
    def provider_id(self) -> ProviderId:
        return PROVIDER_ANTIGRAVITY

    @property
    def display_name(self) -> str:
        return "Antigravity"

    @property
    def executable(self) -> str:
        return "agy"

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self.provider_id,
            display_name=self.display_name,
            supports_interactive=True,
            supports_headless=True,
            supports_native_resume=True,
            supports_structured_output=True,
            supports_mcp=True,
            supports_usage_metrics=True,
        )

    def probe(self) -> ProviderDiagnostic:
        capabilities = self.get_capabilities()
        resolved_path = self._which(self.executable)

        if not resolved_path:
            return ProviderDiagnostic(
                provider_id=self.provider_id,
                display_name=self.display_name,
                executable=self.executable,
                installed=False,
                resolved_path=None,
                version=None,
                authentication_status=AuthenticationStatus.UNKNOWN,
                capabilities=capabilities,
                diagnostics=["Not found in PATH"],
            )

        # 1. Version probe (passive --version)
        version_result = self._runner.run([resolved_path, "--version"], timeout=5.0)
        version: str | None = None
        diagnostics: list[str] = []

        if version_result.success:
            version = _extract_version(version_result.stdout)
            if not version:
                diagnostics.append("Version output could not be parsed")
        else:
            if version_result.timed_out:
                diagnostics.append("Version probe timed out")
            else:
                diagnostics.append("Version probe failed")

        # 2. Authentication probe:
        # Passive-only: agy does not offer a non-model auth status command.
        # Report UNKNOWN without running any prompts.
        diagnostics.append("Passive authentication probe not supported")

        return ProviderDiagnostic(
            provider_id=self.provider_id,
            display_name=self.display_name,
            executable=self.executable,
            installed=True,
            resolved_path=resolved_path,
            version=version,
            authentication_status=AuthenticationStatus.UNKNOWN,
            capabilities=capabilities,
            diagnostics=diagnostics,
        )


class AntigravityRuntimeAdapter(ProviderRuntimeAdapter):
    """Runtime adapter for launching Google Antigravity interactive sessions."""

    @property
    def provider_id(self) -> ProviderId:
        return PROVIDER_ANTIGRAVITY

    @property
    def display_name(self) -> str:
        return "Antigravity"

    @property
    def executable(self) -> str:
        return "agy"

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self.provider_id,
            display_name=self.display_name,
            supports_interactive=True,
            supports_headless=True,
            supports_native_resume=True,
            supports_structured_output=True,
            supports_mcp=True,
            supports_usage_metrics=True,
        )

    def get_native_capabilities(self) -> NativeSessionCapabilities:
        return NativeSessionCapabilities(
            supports_exact_resume=True,
            can_capture_native_id_during_bootstrap=True,
            can_resume_with_followup_context=True,
            requires_model_turn_for_handoff_resume=True,
            supports_managed_new_session=True,
        )

    def build_exact_resume(
        self, project_root: Path, executable_path: str, native_session_id: str
    ) -> LaunchSpecification:
        if not valid_native_id(native_session_id):
            raise NativeResumeError("Invalid native session identifier.")
        return LaunchSpecification(
            provider_id=self.provider_id,
            executable=executable_path,
            cwd=project_root,
            argv=[executable_path, "--conversation", native_session_id],
            native_session_id=native_session_id,
        )

    def build_launch_spec(
        self,
        project_root: Path,
        executable_path: str,
        prompt: str | None = None,
    ) -> LaunchSpecification:
        """Build argument vector for native Antigravity launch.

        Raises UnsupportedPromptError if an initial prompt is provided.
        """
        if prompt is not None and prompt.strip():
            raise UnsupportedPromptError(
                "Antigravity does not currently expose a supported interactive "
                "initial-prompt launch path through CortexShift.\n\n"
                "Launch without --prompt and enter the prompt in the native Antigravity UI."
            )

        return LaunchSpecification(
            provider_id=self.provider_id,
            executable=executable_path,
            cwd=project_root,
            argv=[executable_path],
            interactive=True,
            initial_prompt_supported=False,
            prompt_supplied=False,
        )


ANTIGRAVITY_BOOTSTRAP_PREFIX = """This is a CortexShift handoff bootstrap.

Analyze and ingest the task context.
Remain read-only.
Do not modify files.
Do not run mutating commands.
Produce a concise continuation plan.

The same conversation will be resumed immediately
in the native interactive Antigravity UI.

--------------------------------------------------
"""


def _parse_bootstrap_metadata(stdout: str) -> tuple[str | None, str | None]:
    """Extract only the required machine fields from Antigravity's JSON bootstrap output.

    Returns a `(conversation_id, status)` tuple. The provider response body, reasoning,
    usage, and tool details are intentionally not read, returned, logged, or persisted.

    Raises:
        HandoffDeliveryError: If the output is not parseable structured JSON.
    """
    text = stdout.strip()
    if not text:
        raise HandoffDeliveryError(
            HandoffFailureCode.BOOTSTRAP_INVALID_OUTPUT.value,
            "The Antigravity handoff bootstrap returned no structured output.",
        )

    try:
        data = json.loads(text)
    except json.JSONDecodeError as err:
        raise HandoffDeliveryError(
            HandoffFailureCode.BOOTSTRAP_INVALID_OUTPUT.value,
            "The Antigravity handoff bootstrap did not return parseable JSON output.",
        ) from err

    if not isinstance(data, dict):
        raise HandoffDeliveryError(
            HandoffFailureCode.BOOTSTRAP_INVALID_OUTPUT.value,
            "The Antigravity handoff bootstrap returned an unexpected JSON structure.",
        )

    raw_conversation_id = data.get("conversation_id")
    conversation_id = raw_conversation_id.strip() if isinstance(raw_conversation_id, str) else None

    raw_status = data.get("status")
    status = raw_status.strip() if isinstance(raw_status, str) else None

    return (conversation_id or None), status


class AntigravityHandoffAdapter(ProviderHandoffAdapter):
    """Delivers canonical handoff context to Antigravity via a safe two-stage flow.

    Antigravity's documented native interactive startup does not expose the same direct
    positional initial-prompt path as Claude Code and Codex. Rather than emulating
    keystrokes, scraping the TUI, or silently dropping the handoff, CortexShift uses two
    documented native capabilities:

    1. A read-only headless planning turn (`--mode=plan -p <context> --output-format json`)
       that ingests the canonical context and produces a continuation plan. Plan mode is
       required here because the purpose of the first turn is context ingestion and
       planning, never unattended workspace mutation.
    2. An interactive resume of that same conversation (`--conversation <id>`) so the user
       lands in the native TUI with the handoff already loaded.

    Only `conversation_id` and `status` are read from the bootstrap output; the response
    body is discarded. Permission bypass flags are never used.
    """

    def __init__(
        self,
        headless_runner: HeadlessProviderRunner | None = None,
        runtime_adapter: "AntigravityRuntimeAdapter | None" = None,
        timeout: float = DEFAULT_HEADLESS_TIMEOUT_SECONDS,
    ) -> None:
        self._headless = headless_runner or SubprocessHeadlessProviderRunner()
        self._runtime = runtime_adapter or AntigravityRuntimeAdapter()
        self._timeout = timeout

    @property
    def provider_id(self) -> ProviderId:
        return PROVIDER_ANTIGRAVITY

    @property
    def display_name(self) -> str:
        return self._runtime.display_name

    @property
    def executable(self) -> str:
        return self._runtime.executable

    @property
    def delivery_strategy(self) -> HandoffDeliveryStrategy:
        return HandoffDeliveryStrategy.PLAN_BOOTSTRAP_THEN_RESUME

    @property
    def bootstrap_model_turn_required(self) -> bool:
        return True

    def prepare_delivery(
        self,
        executable_path: str,
        project_root: Path,
        rendered_context: str,
        native_session_id: str | None = None,
    ) -> HandoffDeliveryPreparation:
        """Run the read-only plan bootstrap, then prepare interactive conversation resume."""
        if native_session_id is not None and not valid_native_id(native_session_id):
            raise NativeResumeError("Invalid native session identifier.")
        bootstrap_prompt = f"{ANTIGRAVITY_BOOTSTRAP_PREFIX}\n{rendered_context}"

        result = self._headless.run_headless(
            argv=[
                executable_path,
                "--mode=plan",
                "-p",
                bootstrap_prompt,
                "--output-format",
                "json",
                *(["--conversation", native_session_id] if native_session_id else []),
            ],
            cwd=project_root,
            timeout=self._timeout,
            env={"CORTEXSHIFT_MCP_READ_ONLY": "1"},
        )

        if result.timed_out:
            raise HandoffDeliveryError(
                HandoffFailureCode.BOOTSTRAP_TIMEOUT.value,
                "The Antigravity handoff bootstrap timed out before returning a plan.",
            )

        if result.not_found:
            raise HandoffDeliveryError(
                HandoffFailureCode.SPAWN_FAILED.value,
                "The Antigravity executable could not be started for the handoff bootstrap.",
            )

        if result.exit_code != 0:
            raise HandoffDeliveryError(
                HandoffFailureCode.BOOTSTRAP_FAILED.value,
                "The Antigravity handoff bootstrap exited unsuccessfully.",
            )

        conversation_id, status = _parse_bootstrap_metadata(result.stdout)

        if status != "SUCCESS":
            raise HandoffDeliveryError(
                HandoffFailureCode.BOOTSTRAP_FAILED.value,
                "The Antigravity handoff bootstrap did not report a successful status.",
            )

        if not valid_native_id(conversation_id):
            raise HandoffDeliveryError(
                HandoffFailureCode.BOOTSTRAP_INVALID_OUTPUT.value,
                "The Antigravity handoff bootstrap did not return a conversation identifier.",
            )

        assert conversation_id is not None
        if native_session_id is not None and conversation_id != native_session_id:
            raise HandoffDeliveryError(
                HandoffFailureCode.BOOTSTRAP_INVALID_OUTPUT.value,
                "Antigravity returned a different conversation; refusing continuity fallback.",
            )

        launch_spec = LaunchSpecification(
            provider_id=self.provider_id,
            executable=executable_path,
            cwd=project_root,
            argv=[executable_path, "--conversation", conversation_id],
            native_session_id=conversation_id,
            interactive=True,
            initial_prompt_supported=False,
            prompt_supplied=False,
            metadata={"resumed_conversation": True},
        )

        return HandoffDeliveryPreparation(
            launch_spec=launch_spec,
            native_session_id=conversation_id,
            bootstrap_performed=True,
        )


ANTIGRAVITY_MCP_CONFIG_REL_PATH = Path(".agents/mcp_config.json")

CORTEXSHIFT_ANTIGRAVITY_MCP_SERVER = {
    "command": "cortexshift",
    "args": ["mcp", "serve"],
}

ANTIGRAVITY_MCP_MISSING_NOTICE = (
    "CortexShift MCP is not configured for Antigravity in this workspace.\n\n"
    "Run:\n  cortexshift mcp setup antigravity"
)


def is_antigravity_mcp_configured(project_root: Path) -> bool:
    """Check if Antigravity workspace MCP config contains the cortexshift server."""
    config_path = project_root / ANTIGRAVITY_MCP_CONFIG_REL_PATH
    if not config_path.is_file():
        return False
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return False
        servers = data.get("mcpServers")
        if not isinstance(servers, dict):
            return False
        entry = servers.get("cortexshift")
        return (
            isinstance(entry, dict)
            and entry.get("command") == CORTEXSHIFT_ANTIGRAVITY_MCP_SERVER["command"]
            and entry.get("args") == CORTEXSHIFT_ANTIGRAVITY_MCP_SERVER["args"]
        )
    except Exception:
        return False


def setup_antigravity_mcp(
    project_root: Path,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Safely configure project-local .agents/mcp_config.json for Antigravity.

    Preserves unrelated MCP servers and top-level keys.
    Fails on configuration conflicts unless force=True.
    Supports dry_run without modifying the filesystem.

    Returns:
        A dictionary describing the action performed and configuration details.

    Raises:
        CortexShiftError: If the existing file contains invalid JSON or has a conflict.
    """
    config_path = project_root / ANTIGRAVITY_MCP_CONFIG_REL_PATH
    target_entry = dict(CORTEXSHIFT_ANTIGRAVITY_MCP_SERVER)

    if not config_path.is_file():
        new_data: dict[str, Any] = {
            "mcpServers": {
                "cortexshift": target_entry,
            }
        }
        if not dry_run:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(json.dumps(new_data, indent=2) + "\n", encoding="utf-8")
        return {
            "action": "created",
            "path": str(config_path),
            "changed": True,
            "dry_run": dry_run,
            "servers": ["cortexshift"],
        }

    # File exists: read and parse safely
    raw_content = config_path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw_content)
    except json.JSONDecodeError as err:
        raise CortexShiftError(
            f"Cannot safely configure MCP: {config_path} contains invalid JSON."
        ) from err

    if not isinstance(data, dict):
        raise CortexShiftError(
            f"Cannot safely configure MCP: {config_path} must contain a JSON object."
        )

    mcp_servers = data.get("mcpServers")
    if mcp_servers is None:
        mcp_servers = {}
        data["mcpServers"] = mcp_servers
    elif not isinstance(mcp_servers, dict):
        raise CortexShiftError(
            f"Cannot safely configure MCP: 'mcpServers' in {config_path} must be a JSON object."
        )

    existing_entry = mcp_servers.get("cortexshift")
    if existing_entry == target_entry:
        return {
            "action": "noop",
            "path": str(config_path),
            "changed": False,
            "dry_run": dry_run,
            "servers": list(mcp_servers.keys()),
        }

    if existing_entry is not None and not force:
        raise CortexShiftError(
            f"Conflicting configuration for 'cortexshift' already exists in {config_path}.\n"
            "Use --force to overwrite only the 'cortexshift' entry while preserving other servers."
        )

    mcp_servers["cortexshift"] = target_entry

    if not dry_run:
        # Atomic write: write to temp file then replace
        temp_path = config_path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        temp_path.replace(config_path)

    return {
        "action": "updated",
        "path": str(config_path),
        "changed": True,
        "dry_run": dry_run,
        "servers": list(mcp_servers.keys()),
    }
