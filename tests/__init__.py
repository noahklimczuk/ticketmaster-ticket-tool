"""Test package. Library logging is silenced so the test output stays readable."""

from __future__ import annotations

import logging

logging.disable(logging.CRITICAL)
