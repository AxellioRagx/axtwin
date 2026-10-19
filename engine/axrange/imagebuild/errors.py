"""Shared error type for the image build pipeline."""
from __future__ import annotations


class ImageBuildError(Exception):
    """Any failure in the image build pipeline.

    `stage` names the build-flow step (docs/IMAGE_BUILD.md §3) that failed, so
    the CLI can render where the build stopped.
    """

    def __init__(self, message: str, *, stage: str = "build") -> None:
        super().__init__(message)
        self.stage = stage
