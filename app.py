import sys
from pathlib import Path

# Ensure src/ is on path when running from repository
ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from voicecontrol.main import main  # noqa: E402


if __name__ == "__main__":
    main()
