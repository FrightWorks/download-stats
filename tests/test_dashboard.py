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

    def test_split_downloads(self):
        s = dashboard.summarize(FIXTURE, RULES)
        self.assertEqual(dashboard.split_downloads(s["versions"][0]["files"]), (16, 3, 3))
        self.assertEqual(dashboard.split_downloads(s["versions"][1]["files"]), (10, 0, 0))

    def test_central_time(self):
        utc = datetime.timezone.utc
        self.assertEqual(dashboard.format_central(datetime.datetime(2026, 11, 12, 0, 32, tzinfo=utc)),
                         "Nov 11, 2026, 6:32 PM CST")
        self.assertEqual(dashboard.format_central(datetime.datetime(2026, 7, 1, 17, 5, tzinfo=utc)),
                         "Jul 1, 2026, 12:05 PM CDT")
        # DST ends Sun 2026-11-01 at 07:00 UTC
        self.assertEqual(dashboard.central_time(datetime.datetime(2026, 11, 1, 6, 59, tzinfo=utc))[1], "CDT")
        self.assertEqual(dashboard.central_time(datetime.datetime(2026, 11, 1, 7, 0, tzinfo=utc))[1], "CST")
        # DST starts Sun 2026-03-08 at 08:00 UTC
        self.assertEqual(dashboard.central_time(datetime.datetime(2026, 3, 8, 8, 0, tzinfo=utc))[1], "CDT")

    def test_load_trend(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(dashboard.load_trend(d), [])
            for stamp, n in (("2026-03-05T050607Z", 9), ("2026-03-04T050607Z", 4)):
                now = datetime.datetime.strptime(stamp, "%Y-%m-%dT%H%M%SZ").replace(tzinfo=datetime.timezone.utc)
                rels = [{"tag_name": "v1", "assets": [asset("a.zip", n), asset("b", 1)]}]
                dashboard.save_snapshot(rels, "o/r", d, now)
            with open(os.path.join(d, "junk.json"), "w") as f:
                f.write("{")
            self.assertEqual(dashboard.load_trend(d),
                             [("2026-03-04T05:06:07Z", 5), ("2026-03-05T05:06:07Z", 10)])

    def test_render_and_snapshot(self):
        now = datetime.datetime(2026, 11, 12, 0, 32, tzinfo=datetime.timezone.utc)
        s = dashboard.summarize(FIXTURE, RULES)
        page = dashboard.render(s, "o/r", now)
        for text in ("v1.1", "Windows zip", "mystery.bin", "unclassified", ">32<", ">26<",
                     "6:32 PM CST", "github.com/o/r/releases", "trend chart appears after a few runs",
                     "<details open>", "prefers-color-scheme:dark"):
            self.assertIn(text, page)
        self.assertEqual(page.count("<details open>"), 1)
        self.assertNotIn("<script", page)
        self.assertNotIn("<polyline", page)
        for bad in ("src=", "@import", "url("):
            self.assertNotIn(bad, page)
        page = dashboard.render(s, "o/r", now, [("2026-11-11T00:00:00Z", 20), ("2026-11-12T00:00:00Z", 32)])
        self.assertIn("<polyline", page)
        self.assertNotIn("appears after a few runs", page)
        with tempfile.TemporaryDirectory() as d:
            now = datetime.datetime(2026, 3, 4, 5, 6, 7, tzinfo=datetime.timezone.utc)
            path = dashboard.save_snapshot(FIXTURE, "o/r", d, now)
            self.assertTrue(path.endswith("2026-03-04T050607Z.json"))
            with open(path) as f:
                snap = json.load(f)
            self.assertEqual(snap["releases"][1]["assets"][0]["download_count"], 5)

    def test_markdown(self):
        md = dashboard.render_markdown(dashboard.summarize(FIXTURE, RULES), "o/r", "2026-03-04 05:06 UTC")
        self.assertIn("**32** total downloads", md)
        self.assertIn("Last updated 2026-03-04 05:06 UTC", md)
        self.assertIn("| Windows zip | 2 | 15 |", md)
        self.assertIn("### v1.1", md)
        self.assertLess(md.index("### v1.1"), md.index("### v1.0"))
        self.assertIn("| mystery.bin | unclassified | 100 B | 3 |", md)
        self.assertNotIn("\r", md)

if __name__ == "__main__":
    unittest.main()
