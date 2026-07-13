#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Stable command wrapper for the Open Nova daily production pipeline."""

import os
import sys

sys.dont_write_bytecode = True

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, SRC_DIR)

from data_foundation.pipeline import command_main


if __name__ == "__main__":
    sys.exit(command_main())
