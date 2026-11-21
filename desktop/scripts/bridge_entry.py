"""Entry point for the frozen Desktop bridge and its native workers."""

from cortexshift.desktop.bridge import main

if __name__ == "__main__":
    raise SystemExit(main())
