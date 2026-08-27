import sys
from pathlib import Path

# The modules are installed flat into one directory and import each other by
# bare name, so the tests put that directory on the path the same way.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "screen_sleep"))
