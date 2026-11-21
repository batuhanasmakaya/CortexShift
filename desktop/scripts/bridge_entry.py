"""Entry point for the frozen desktop bridge."""

import sys

from cortexshift.desktop.bridge import serve

if __name__ == "__main__":
    serve(sys.stdin.buffer, sys.stdout.buffer)
