"""Offline experimental classifiers; nothing here is wired into FastAPI."""

from .row_classifier import RowClassifier, load_row_classifier

__all__ = ["RowClassifier", "load_row_classifier"]

