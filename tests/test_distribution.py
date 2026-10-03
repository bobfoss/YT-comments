"""Keep the wheel's installation declaration aligned with the plugin contract."""
import importlib
import json
from pathlib import Path
import tomllib
import unittest


class DistributionTests(unittest.TestCase):
    def test_installation_manifest_matches_plugin(self):
        root = Path(__file__).resolve().parents[1]
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        entries = project["entry-points"]["yt_library.plugins"]
        self.assertEqual(len(entries), 1)
        plugin_id, entry = next(iter(entries.items()))
        module, factory = entry.split(":")
        plugin = getattr(importlib.import_module(module), factory)()
        package = module.split(".")[0]
        manifest = json.loads((root / package / "ytl-plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["id"], plugin_id)
        self.assertEqual(plugin.plugin_id, plugin_id)
        self.assertEqual(plugin.plugin_version, project["version"])
        self.assertEqual(manifest["plugin_api_version"], plugin.plugin_api_version)
        self.assertEqual(set(manifest["required_host_features"]), set(getattr(plugin, "required_host_features", ())))
        self.assertEqual(manifest["browser_api_version"], 2 if getattr(plugin, "browser_assets", ()) else None)
        self.assertIsInstance(manifest["config_template"], dict)
        self.assertEqual(project["license"], "GPL-3.0-or-later")
        for asset in getattr(plugin, "browser_assets", ()):
            self.assertTrue((root / package / asset["path"]).is_file())
