from pathlib import Path
import unittest


class StaticAssetTests(unittest.TestCase):
    def test_single_frontend_contains_current_frontier_graph(self):
        app = Path(__file__).resolve().parents[3] / "src" / "lean_exposition" / "app"
        static = app / "static"
        self.assertTrue((static / "index.html").is_file())
        self.assertTrue((static / "reader.css").is_file())
        self.assertTrue((static / "reader.js").is_file())
        self.assertFalse((app / "static_v7").exists())

        script = (static / "reader.js").read_text()
        styles = (static / "reader.css").read_text()
        self.assertIn("n.is_frontier", script)
        self.assertIn(".hdg-node .visible-dot", styles)
        self.assertIn(".hdg-node .hit-dot", styles)
        self.assertNotIn("input_version", script)
        self.assertNotIn("random-v1", script)


if __name__ == "__main__":
    unittest.main()
