import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FrontendContractTests(unittest.TestCase):
    def test_javascript_static_id_references_exist_in_html(self):
        html = (ROOT / "static/index.html").read_text(encoding="utf-8")
        html_ids = set(re.findall(r'\bid="([^"]+)"', html))
        referenced_ids = set()
        for relative_path in (
            "static/app.js",
            "static/js/result-map.js",
            "static/js/ui-utils.js",
        ):
            source = (ROOT / relative_path).read_text(encoding="utf-8")
            referenced_ids.update(re.findall(r'byId\("([^"]+)"\)', source))

        self.assertEqual(set(), referenced_ids - html_ids)

    def test_map_dependencies_are_local_and_use_patched_maplibre(self):
        html = (ROOT / "static/index.html").read_text(encoding="utf-8")

        self.assertNotIn("unpkg.com", html)
        self.assertNotIn("maplibre-gl@5.24.0", html)
        self.assertIn("/static/vendor/maplibre-gl/maplibre-gl.css?v=6.10.0", html)
        self.assertTrue((ROOT / "static/vendor/maplibre-gl/maplibre-gl.mjs").is_file())
        self.assertTrue((ROOT / "static/vendor/maplibre-gl/maplibre-gl-shared.mjs").is_file())
        self.assertTrue((ROOT / "static/vendor/maplibre-gl/maplibre-gl-worker.mjs").is_file())

    def test_relative_javascript_imports_resolve_to_files(self):
        import_pattern = re.compile(r'\b(?:from|import)\s*(?:\(\s*)?["\']([^"\']+)["\']')
        missing = []
        for source_path in (ROOT / "static").rglob("*"):
            if source_path.suffix not in {".js", ".mjs"}:
                continue
            source = source_path.read_text(encoding="utf-8")
            for specifier in import_pattern.findall(source):
                if not specifier.startswith("."):
                    continue
                dependency = (source_path.parent / specifier.split("?", 1)[0]).resolve()
                if not dependency.is_file():
                    missing.append((str(source_path.relative_to(ROOT)), specifier))

        self.assertEqual([], missing)


if __name__ == "__main__":
    unittest.main()
