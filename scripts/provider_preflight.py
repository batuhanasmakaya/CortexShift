"""Passive version/help-only provider contract check; never launches a model."""

import shutil
import subprocess

CONTRACTS = {
    "claude": [(("--help",), ("--session-id", "--resume", "--mcp-config"))],
    "codex": [
        (("--help",), ("resume", "exec", "--config")),
        (("exec", "--help"), ("--sandbox", "--json")),
        (("exec", "resume", "--help"), ("--json",)),
    ],
    "agy": [(("--help",), ("--mode", "--output-format", "--conversation", "-p"))],
}


def main() -> int:
    failed = False
    for provider, cases in CONTRACTS.items():
        executable = shutil.which(provider)
        if not executable:
            print(f"{provider}: unavailable; real-provider validation deferred")
            continue
        try:
            version = subprocess.run(
                [executable, "--version"], capture_output=True, text=True, timeout=15, check=True
            )
            # Do not dump arbitrary provider output, environment, or local paths.
            import re

            match = re.search(r"\b\d+\.\d+\.\d+\b", version.stdout)
            for arguments, flags in cases:
                result = subprocess.run(
                    [executable, *arguments], capture_output=True, text=True, timeout=15, check=True
                )
                missing = [flag for flag in flags if flag not in result.stdout + result.stderr]
                if missing:
                    raise ValueError(f"Missing help surfaces: {', '.join(missing)}")
            print(f"{provider}: {match[0] if match else 'version unknown'}; help contract passed")
        except (subprocess.SubprocessError, OSError, ValueError) as error:
            print(f"{provider}: contract failed ({type(error).__name__}); review native help")
            failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
