"""Public entry points for loading declaration facts from LC or native Lean."""
from .common import assemble_workspace
from .lc import LCRepositoryInput, load_lc_workspace
from .native import load_native

__all__ = ["LCRepositoryInput", "assemble_workspace", "load_lc_workspace", "load_native"]
