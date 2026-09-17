"""Public entry points for loading declaration facts from LC or native Lean."""
from .common import assemble_workspace
from .lc import LCRepositoryAdapter, LCRepositoryInput, load_lc_workspace
from .native import NativeRepositoryAdapter, load_native
from .source import (
    SourceCommandCoverage, SourceCommandIssue, SourceInventoryFile,
    build_provisional_source_bundle, consume_text_ast_json, iter_text_ast_jsonl,
    merge_source_inventory, provisional_source_adapter,
)

__all__ = ["LCRepositoryAdapter", "LCRepositoryInput", "NativeRepositoryAdapter",
           "SourceCommandCoverage", "SourceCommandIssue", "SourceInventoryFile",
           "assemble_workspace", "build_provisional_source_bundle",
           "consume_text_ast_json", "iter_text_ast_jsonl", "load_lc_workspace",
           "load_native", "merge_source_inventory", "provisional_source_adapter"]
