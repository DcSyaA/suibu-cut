"""Entry point for 随舞剪辑台 (SUIBU CUT)."""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running from the source folder and from the portable runtime.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from svdj.ui import main  # noqa: E402


if __name__ == "__main__":
    main()
