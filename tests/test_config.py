"""Loading and validating apps.json."""
import json
import unittest

import support  # noqa: F401  (sets up sys.path and temp dirs)
from applauncher import config
from applauncher.config import App


class LoadValidTests(support.TmpDirTestCase):
    def test_full_entry(self):
        path = self.write_json("apps.json", {"apps": [{
            "name": "Thing", "command": "C:\\x\\thing.exe", "args": ["-a", "b c"],
            "working_dir": "C:\\x", "icon": "C:\\x\\thing.ico,2", "needs_console": True,
            "window": "Minimized", "launch_all": False, "match": ["one", " two "], "_note": "hi",
        }]})
        result = config.load_apps(path)
        self.assertTrue(result.ok)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.warnings, [])
        self.assertEqual(result.apps, [App(
            name="Thing", command="C:\\x\\thing.exe", args=("-a", "b c"), working_dir="C:\\x",
            icon="C:\\x\\thing.ico,2", needs_console=True, window="minimized", launch_all=False,
            match=("one", "two"), note="hi")])

    def test_defaults(self):
        path = self.write_json("apps.json", {"apps": [{"name": "A", "command": "a.exe"}]})
        (app,) = config.load_apps(path).apps
        self.assertEqual(app.args, ())
        self.assertEqual(app.working_dir, "")
        self.assertEqual(app.icon, "")
        self.assertFalse(app.needs_console)
        self.assertEqual(app.window, "normal")
        self.assertTrue(app.launch_all)
        self.assertEqual(app.match, ())

    def test_args_may_be_one_string_and_cwd_is_an_alias(self):
        path = self.write_json("apps.json", {"apps": [
            {"name": "A", "command": "a.exe", "args": "/c \"x y\"", "cwd": "C:\\w"}]})
        (app,) = config.load_apps(path).apps
        self.assertEqual(app.args, '/c "x y"')
        self.assertEqual(app.working_dir, "C:\\w")

    def test_help_and_underscore_keys_are_comments(self):
        path = self.write_json("apps.json", {"_help": {"x": 1}, "_other": [1, 2], "apps": [
            {"name": "A", "command": "a.exe", "_note": "n", "_anything": 3}]})
        result = config.load_apps(path)
        self.assertTrue(result.ok)
        self.assertEqual(result.warnings, [])
        self.assertEqual(len(result.apps), 1)

    def test_plain_top_level_list_is_accepted(self):
        path = self.write_json("apps.json", [{"name": "A", "command": "a.exe"}])
        self.assertEqual([a.name for a in config.load_apps(path).apps], ["A"])

    def test_empty_list_is_fine(self):
        result = config.load_apps(self.write_json("apps.json", {"apps": []}))
        self.assertTrue(result.ok)
        self.assertEqual(result.apps, [])

    def test_utf8_bom_from_notepad_is_tolerated(self):
        path = self.tmp / "apps.json"
        path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"apps": [{"name": "A", "command": "a.exe"}]}).encode())
        self.assertEqual(len(config.load_apps(path).apps), 1)


class BrokenFileTests(support.TmpDirTestCase):
    def test_broken_json_reports_line_and_column_and_is_not_ok(self):
        path = self.write_text("apps.json", '{\n  "apps": [\n    {"name": "A", "command": "a.exe"},\n  ]\n}\n')
        result = config.load_apps(path)
        self.assertFalse(result.ok)
        self.assertEqual(result.apps, [])
        self.assertIn("not valid JSON", result.message)
        self.assertRegex(result.message, r"line 3, column \d+")

    def test_garbage(self):
        result = config.load_apps(self.write_text("apps.json", "this is not json"))
        self.assertFalse(result.ok)
        self.assertTrue(result.errors)

    def test_empty_file(self):
        result = config.load_apps(self.write_text("apps.json", ""))
        self.assertFalse(result.ok)

    def test_missing_file(self):
        result = config.load_apps(self.tmp / "nope.json")
        self.assertFalse(result.ok)
        self.assertIn("not found", result.message)

    def test_wrong_shapes(self):
        for data, needle in (({"apps": "x"}, "must be a list"), ({"nothing": []}, 'no "apps" list'),
                             ("just text", "must be an object"), (42, "must be an object")):
            with self.subTest(data=data):
                result = config.load_apps(self.write_json("apps.json", data))
                self.assertFalse(result.ok)
                self.assertIn(needle, result.message)

    def test_non_utf8_bytes(self):
        path = self.tmp / "apps.json"
        path.write_bytes(b'{"apps": [{"name": "\xff\xfe", "command": "a"}]}')
        result = config.load_apps(path)
        self.assertFalse(result.ok)
        self.assertIn("Could not read", result.message)


class BadEntryTests(support.TmpDirTestCase):
    def test_bad_entries_are_skipped_and_named_but_good_ones_load(self):
        path = self.write_json("apps.json", {"apps": [
            {"name": "Good", "command": "good.exe"},
            {"command": "no-name.exe"},
            {"name": "NoCommand"},
            {"name": "BadArgs", "command": "x.exe", "args": [1, 2]},
            {"name": "BadBool", "command": "x.exe", "needs_console": "yes"},
            {"name": "BadWindow", "command": "x.exe", "window": "fullscreen"},
            {"name": "BadMatch", "command": "x.exe", "match": 5},
            "not an object",
            {"name": "Good2", "command": "good2.exe"},
        ]})
        result = config.load_apps(path)
        self.assertTrue(result.ok)
        self.assertEqual([a.name for a in result.apps], ["Good", "Good2"])
        self.assertEqual(len(result.errors), 7)
        joined = "\n".join(result.errors)
        for needle in ("entry 2", "'name' is required", "NoCommand", "BadArgs", "BadBool", "BadWindow",
                       "BadMatch", "entry 8"):
            self.assertIn(needle, joined)
        self.assertIn("(+6 more)", result.message)

    def test_unknown_key_is_a_warning_not_an_error(self):
        path = self.write_json("apps.json", {"apps": [{"name": "A", "command": "a.exe", "needsconsole": True}]})
        result = config.load_apps(path)
        self.assertEqual(len(result.apps), 1)
        self.assertEqual(result.errors, [])
        self.assertIn("needsconsole", result.warnings[0])

    def test_blank_name_or_command_counts_as_missing(self):
        path = self.write_json("apps.json", {"apps": [{"name": "  ", "command": "a.exe"},
                                                       {"name": "A", "command": ""}]})
        result = config.load_apps(path)
        self.assertEqual(result.apps, [])
        self.assertEqual(len(result.errors), 2)


class SkeletonTests(support.TmpDirTestCase):
    def test_ensure_file_creates_a_loadable_starter_and_never_overwrites(self):
        path = self.tmp / "apps.json"
        self.assertTrue(config.ensure_file(path))
        result = config.load_apps(path)
        self.assertTrue(result.ok)
        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.apps), 1)
        self.assertIn("_help", json.loads(path.read_text(encoding="utf-8")))
        path.write_text('{"apps": []}', encoding="utf-8")
        self.assertFalse(config.ensure_file(path))
        self.assertEqual(path.read_text(encoding="utf-8"), '{"apps": []}')


class ShippedFileTests(unittest.TestCase):
    """The apps.json that ships with the launcher."""

    @classmethod
    def setUpClass(cls):
        cls.path = support.ROOT / "apps.json"
        cls.raw = json.loads(cls.path.read_text(encoding="utf-8"))
        cls.result = config.load_apps(cls.path)

    def test_loads_cleanly(self):
        self.assertTrue(self.result.ok)
        self.assertEqual(self.result.errors, [])
        self.assertEqual(self.result.warnings, [])

    def test_help_comes_first_and_documents_every_field(self):
        self.assertEqual(next(iter(self.raw)), "_help")
        for field in ("name", "command", "args", "working_dir", "icon", "needs_console"):
            self.assertIn(field, self.raw["_help"])

    def test_the_four_requested_apps_come_first_in_order(self):
        names = [a.name for a in self.result.apps]
        self.assertEqual(names[:4], ["UI Report Tool", "UtilityBelt", "UtilityBelt Mini", "Screener shell"])

    def test_suggested_entries_are_marked_and_kept_out_of_launch_all(self):
        suggested = [a for a in self.result.apps[4:]]
        self.assertTrue(suggested)
        for app in suggested:
            self.assertIn("Suggested", app.note, app.name)
            self.assertFalse(app.launch_all, app.name)

    def test_at_most_nine_apps_have_number_keys_but_list_is_short(self):
        self.assertLessEqual(len(self.result.apps), 9)


if __name__ == "__main__":
    unittest.main()
