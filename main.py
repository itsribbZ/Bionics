"""Bionics - AI Desktop Automation Agent

Launch the Bionics GUI application.
"""

import faulthandler
import logging
import os
import sys
from pathlib import Path

# Enable faulthandler to catch C-level segfaults
faulthandler.enable()

# Setup logging
LOG_DIR = Path(__file__).parent / "audit"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "bionics.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("bionics")


def check_api_key() -> bool:
    """Verify that the Anthropic API key is available."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        print("\n[ERROR] ANTHROPIC_API_KEY not found in environment variables.")
        print("Set it with:  setx ANTHROPIC_API_KEY \"sk-ant-your-key-here\"")
        print("Then restart your terminal.\n")
        return False
    logger.info("API key found")
    return True


def main():
    """Launch the Bionics application."""
    import traceback

    from PyQt6.QtGui import QFont
    from PyQt6.QtWidgets import QApplication

    from gui.app import BionicsWindow

    # Catch ALL uncaught exceptions including from Qt callbacks
    def exception_hook(exc_type, exc_value, exc_tb):
        msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        logger.error(f"Uncaught exception:\n{msg}")
        print(f"\n[CRASH]\n{msg}", file=sys.stderr)
    sys.excepthook = exception_hook

    logger.info("=" * 60)
    logger.info("BIONICS - AI Desktop Automation Agent v0.8.1")
    logger.info("=" * 60)

    if not check_api_key():
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setApplicationName("Bionics")
    app.setFont(QFont("Segoe UI", 10))

    window = BionicsWindow()
    window.show()

    logger.info("GUI launched")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
