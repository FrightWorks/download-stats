import datetime
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
import dashboard  # noqa: E402

RULES = dashboard.load_rules(os.path.join(ROOT, "asset-types.json"))


def asset(name, count, size=100):
    return {"name": name, "download_count": count, "size": size}


FIXTURE = [
    {"tag_name": "v1.0", "published_at": "2026-01-01T00:00:00Z", "assets": [
        asset("FrightWorks-1.0.zip", 10)]},
    {"tag_name": "v1.1", "published_at": "2026-02-01T00:00:00Z", "assets": [
        asset("FrightWorks-1.1.zip", 5), asset("FrightWorks-win-Setup.exe", 7),
        asset("FrightWorks-1.1-linux-full.nupkg", 2), asset("FrightWorks-1.1-full.nupkg", 1),
        asset("mystery.bin", 3), asset("FrightWorks.AppImage", 4)]},
]


class Tests(unittest.TestCase):
    def test_classify(self):
        c = lambda n: dashboard.classify(n, RULES)[0]
        self.assertEqual(c("FrightWorks-9.9.zip"), "Windows zip")
        self.assertEqual(c("FrightWorks-1.1-linux-full.nupkg"), "Linux update package")
        self.assertEqual(c("FrightWorks-1.1-full.nupkg"), "Windows update package")
        self.assertEqual(c("RELEASES-linux"), "Linux update feed")
        self.assertEqual(c("RELEASES"), "Windows update feed")
        self.assertEqual(dashboard.classify("mystery.bin", RULES), ("unclassified", "unclassified"))

    def test_summary(self):
        s = dashboard.summarize(FIXTURE, RULES)
        self.assertEqual(s["total"], 32)
        self.assertEqual([v["tag"] for v in s["versions"]], ["v1.1", "v1.0"])
        self.assertEqual([v["downloads"] for v in s["versions"]], [22, 10])
        self.assertEqual(s["versions"][0]["date"], "2026-02-01")
        types = {t["type"]: t["downloads"] for t in s["types"]}
        self.assertEqual(types["Windows zip"], 15)
        self.assertEqual(types["unclassified"], 3)
        self.assertEqual(sum(types.values()), s["total"])
        self.assertEqual(s["versions"][0]["files"][0]["name"], "FrightWorks-win-Setup.exe")

    def test_render_and_snapshot(self):
        page = dashboard.render(dashboard.summarize(FIXTURE, RULES), "o/r", "now")
        for text in ("v1.1", "Windows zip", "mystery.bin", "unclassified", '<div class="big">32</div>'):
            self.assertIn(text, page)
        self.assertNotIn("http", page)
        with tempfile.TemporaryDirectory() as d:
            now = datetime.datetime(2026, 3, 4, 5, 6, 7, tzinfo=datetime.timezone.utc)
            path = dashboard.save_snapshot(FIXTURE, "o/r", d, now)
            self.assertTrue(path.endswith("2026-03-04T050607Z.json"))
            with open(path) as f:
                snap = json.load(f)
            self.assertEqual(snap["releases"][1]["assets"][0]["download_count"], 5)


if __name__ == "__main__":
    unittest.main()
