"""Test package init -- exists to redirect runtime/ before anything imports it.

The tests build real Controllers, and a Controller's constructor calls
runtime_status.reset_status(), which writes runtime/status.json with the calling
process's pid. bot_logger writes runtime/logs/bot.log the same way. So running
the suite wrote into the live bot's artefacts: measured on 2026-08-21, one suite
run put ~1000 lines of fixture output ("CAST_FAILED: card 999 ...") into
runtime/analysis/history.log while a real match was being played, and left a
status.json describing the unittest process. Those files are the first place
CLAUDE.md sends you when debugging bot behaviour, so polluting them costs
exactly when it hurts most.

Importing this package sets MTGA_RUNTIME_DIR to a throwaway directory. unittest
imports the package before any test module, so bot modules resolve their paths
from the temp dir even if they cache them at import time.

Limitation worth knowing: running a test file DIRECTLY
(`python tests/test_x.py`) does not import this package, so that path still
writes into the repo's runtime/. Use `python -m unittest discover tests` (what
CLAUDE.md prescribes) or `python -m unittest tests.test_x`. RuntimeIsolationTest
in test_runtime_isolation.py fails loudly if the redirect is not in place.
"""

import atexit
import os
import shutil
import tempfile

_TEST_RUNTIME_DIR = tempfile.mkdtemp(prefix="mtga-test-runtime-")
os.environ["MTGA_RUNTIME_DIR"] = _TEST_RUNTIME_DIR


@atexit.register
def _cleanup_test_runtime_dir() -> None:
    # Best effort: a leftover temp dir is harmless, a crash in atexit is not.
    shutil.rmtree(_TEST_RUNTIME_DIR, ignore_errors=True)
