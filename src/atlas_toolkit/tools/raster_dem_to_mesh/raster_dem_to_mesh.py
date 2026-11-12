"""Compatibility import for the shared heightfield implementation."""
import sys
from importlib import import_module
sys.modules[__name__] = import_module("atlas_toolkit.geom.heightfield.mesh")
