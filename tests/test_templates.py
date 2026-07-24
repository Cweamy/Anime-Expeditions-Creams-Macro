import json
import os
import shutil
import tempfile
import unittest

from core import templates


class TestTemplatesValidation(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.orig_templates_dir = templates.TEMPLATES_DIR
        templates.TEMPLATES_DIR = self.test_dir

    def tearDown(self):
        templates.TEMPLATES_DIR = self.orig_templates_dir
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_save_and_list_valid_template(self):
        templates.save_template("valid_macro", [{"type": "click", "params": {}}])
        template_list = templates.list_templates()
        self.assertEqual(template_list, ["valid_macro"])

    def test_list_templates_ignores_bundle_export_files(self):
        # Save a valid template
        templates.save_template("my_template", [{"type": "click", "params": {}}])

        # Simulate an exported template bundle saved inside TEMPLATES_DIR
        bundle_path = os.path.join(self.test_dir, "AnimeExpeditions-templates-20260724-031314.json")
        bundle_data = {
            "kind": "anime-expeditions-templates",
            "version": 1,
            "exported": "2026-07-24T03:13:14.000Z",
            "templates": {
                "my_template": {"name": "my_template", "blocks": []}
            }
        }
        with open(bundle_path, "w", encoding="utf-8") as f:
            json.dump(bundle_data, f)

        # list_templates should only include 'my_template' and ignore the export bundle
        template_list = templates.list_templates()
        self.assertEqual(template_list, ["my_template"])

    def test_load_template_rejects_invalid_structure(self):
        bundle_path = os.path.join(self.test_dir, "invalid_template.json")
        with open(bundle_path, "w", encoding="utf-8") as f:
            json.dump({"kind": "bundle", "templates": {}}, f)

        loaded = templates.load_template("invalid_template")
        self.assertEqual(loaded, {"name": "invalid_template", "blocks": []})

    def test_load_template_rejects_non_list_blocks(self):
        bad_path = os.path.join(self.test_dir, "bad_blocks.json")
        with open(bad_path, "w", encoding="utf-8") as f:
            json.dump({"name": "bad_blocks", "blocks": "not_a_list"}, f)

        self.assertNotIn("bad_blocks", templates.list_templates())
        loaded = templates.load_template("bad_blocks")
        self.assertEqual(loaded, {"name": "bad_blocks", "blocks": []})


if __name__ == "__main__":
    unittest.main()
