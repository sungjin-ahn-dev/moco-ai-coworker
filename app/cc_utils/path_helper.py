"""
Cross-platform path helper for portable deployment.

Searches for .moco/ directory in this order:
1. Next to project root (portable mode): ../../../.moco/ relative to this file
2. User home directory: ~/.moco/
"""

import os
from pathlib import Path

# Project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def get_moco_dir() -> str:
    """Get .moco config directory path.

    Returns portable path (next to project) if it exists,
    otherwise falls back to ~/.moco/.
    """
    portable_dir = _PROJECT_ROOT.parent / ".moco"
    if portable_dir.exists():
        return str(portable_dir)
    return os.path.join(os.path.expanduser("~"), ".moco")


def get_moco_file(filename: str) -> str:
    """Get full path to a file inside .moco/ directory."""
    return os.path.join(get_moco_dir(), filename)
