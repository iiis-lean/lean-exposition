from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lean_exposition.lean import extract_modules


class ToolBoundaryTests(unittest.TestCase):
    def test_empty_or_duplicate_selection_never_builds(self):
        with patch('lean_exposition.lean.tools._run') as run:
            for modules in ((), ('M', 'M')):
                with self.assertRaisesRegex(ValueError, 'nonempty'):
                    extract_modules('/unused', modules)
            run.assert_not_called()

    def test_unknown_version_requires_explicit_repl(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'M.lean').write_text('def x := 1')
            (root / 'lean-toolchain').write_text('leanprover/lean4:v9.0.0')
            with patch('lean_exposition.lean.tools._run') as run:
                with self.assertRaisesRegex(ValueError, 'supply repl_rev explicitly'):
                    extract_modules(root, ('M',))
                run.assert_not_called()

    def test_local_repl_toolchain_mismatch_never_builds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'M.lean').write_text('def x := 1')
            (root / 'lean-toolchain').write_text('leanprover/lean4:v4.28.0')
            repl = root / 'repl'
            repl.mkdir()
            (repl / 'lean-toolchain').write_text('leanprover/lean4:v4.32.0')
            with patch('lean_exposition.lean.tools._run') as run:
                with self.assertRaisesRegex(ValueError, 'does not match'):
                    extract_modules(root, ('M',), local_repl_path=repl)
                run.assert_not_called()
