"""Use a stable Unicode output encoding for console entry points and pipes."""
import sys


def configure_console_output():
    # Frozen apps ignore Python's environment settings. Configure real streams
    # in process as well, before argparse or localized errors write anything.
    # Leave stdin and streams without reconfigure (e.g. StringIO) unchanged.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")
