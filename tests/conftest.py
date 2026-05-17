"""Pytest configuration for Strix Telegram Bot tests."""

import pytest


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "requires_strix: tests that need the strix package installed")
