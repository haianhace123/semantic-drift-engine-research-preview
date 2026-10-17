"""
Path management for the application.
Provides platform-independent directory paths.
"""

import os
from pathlib import Path


class Paths:
    """
    Centralized path management for the application.
    """

    @staticmethod
    def get_project_root() -> Path:
        """
        Get the project root directory.

        Returns:
            Path object pointing to project root.
        """
        # Get the directory containing this file (src/core/)
        current_dir = Path(__file__).resolve().parent
        # Go up two levels to reach project root
        return current_dir.parent.parent

    @staticmethod
    def get_data_dir() -> Path:
        """
        Get the data directory path.

        Returns:
            Path object pointing to data directory.
        """
        root = Paths.get_project_root()
        data_dir = root / "data" / "raw"
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir

    @staticmethod
    def get_snapshot_path(filename: str) -> str:
        """
        Get full path for a snapshot file.

        Args:
            filename: Name of the snapshot file.

        Returns:
            Full path as string.
        """
        data_dir = Paths.get_data_dir()
        return str(data_dir / filename)

    @staticmethod
    def get_static_dir() -> Path:
        """
        Get the static files directory.

        Returns:
            Path object pointing to static directory.
        """
        root = Paths.get_project_root()
        return root / "src" / "static"