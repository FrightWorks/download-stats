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
        asset("mystery.bin", 3), asset("FrightWorks.AppImage", 4),
        asset("FrightWorks-1.0-full.nupkg", 9), asset("releases.win.json", 20), asset("RELEASES", 6),
        asset("releases.linux.json", 8)]},
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
        self.assertEqual(s["total"], 75)
        self.assertEqual([v["tag"] for v in s["versions"]], ["v1.1", "v1.0"])
        self.assertEqual([v["downloads"] for v in s["versions"]], [65, 10])
        self.assertEqual(s["versions"][0]["date"], "2026-02-01")
        types = {t["type"]: t["downloads"] for t in s["types"]}
        self.assertEqual(types["Windows zip"], 15)
        self.assertEqual(types["unclassified"], 3)
        self.assertEqual(sum(types.values()), s["total"])
        self.assertEqual(s["versions"][0]["files"][0]["name"], "releases.win.json")

    def test_platform_stats(self):
        s = dashboard.summarize(FIXTURE, RULES)
        cur = dashboard.current_version(s["versions"])
        self.assertEqual(cur["tag"], "v1.1")
        st = dashboard.platform_stats(cur)
        self.assertEqual(st["Windows"]["downloads"], 12)
        self.assertEqual(st["Windows"]["parts"], [("Installer", 7), ("Zip", 5)])
        self.assertEqual(st["Windows"]["updates"], 1)  # the 1.0 package in this release is skipped
        self.assertEqual(st["Windows"]["checks"], 20)
        self.assertEqual(st["Linux"]["checks"], 8)
        self.assertEqual(st["Linux"]["downloads"], 4)
        self.assertEqual(st["Linux"]["updates"], 2)
        self.assertEqual(st["Linux"]["update_parts"], [("Full", 2)])
        drafts = [{"tag": "v3", "draft": True, "prerelease": False}, {"tag": "v2", "draft": False, "prerelease": True},
                  {"tag": "v1", "draft": False, "prerelease": False}]
        self.assertEqual(dashboard.current_version(drafts)["tag"], "v1")

    def test_shown_versions_limit(self):
        rels = [{"tag_name": f"v{i}", "published_at": f"2026-01-{i:02d}T00:00:00Z", "assets": [asset("a.zip", i)]}
                for i in range(1, 9)]
        rels.append({"tag_name": "v9", "draft": True, "published_at": "2026-01-09T00:00:00Z", "assets": []})
        shown = dashboard.shown_versions(dashboard.summarize(rels, RULES)["versions"])
        self.assertEqual([v["tag"] for v in shown], ["v8", "v7", "v6", "v5", "v4"])
        page = dashboard.render(dashboard.summarize(rels, RULES), "o/r", "now")
        self.assertNotIn("v3", page)
        self.assertNotIn("v9", page)

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

    def _history(self, d):
        for stamp, zip_n, linux_n in (("2026-03-05T200000Z", 9, 3), ("2026-03-04T200000Z", 4, 1),
                                      ("2026-03-04T080000Z", 2, 1), ("2026-03-06T060000Z", 12, 5)):
            now = datetime.datetime.strptime(stamp, "%Y-%m-%dT%H%M%SZ").replace(tzinfo=datetime.timezone.utc)
            rels = [{"tag_name": "v1", "assets": [asset("FrightWorks-1.zip", zip_n), asset("RELEASES", 1),
                                                  asset("FrightWorks.AppImage", linux_n)]},
                    {"tag_name": "v0", "assets": [asset("FrightWorks-0.zip", 50)]}]
            dashboard.save_snapshot(rels, "o/r", d, now)
        with open(os.path.join(d, "junk.json"), "w") as f:
            f.write("{")

    def test_history(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(dashboard.load_history(d, "v1", RULES), [])
            self._history(d)
            h = dashboard.load_history(d, "v1", RULES)
            self.assertEqual([t for t, _ in h], ["2026-03-04T08:00:00Z", "2026-03-04T20:00:00Z",
                                                 "2026-03-05T20:00:00Z", "2026-03-06T06:00:00Z"])
            self.assertEqual([st["Windows"]["downloads"] for _, st in h], [2, 4, 9, 12])
            # Central dates: Mar 4, Mar 4, Mar 5, Mar 6 -> last-of-day totals 5, 12, 17
            self.assertEqual(dashboard.daily_new(h), [(datetime.date(2026, 3, 5), 7), (datetime.date(2026, 3, 6), 5)])
            self.assertEqual(dashboard.recent_change(h, "Windows"), (10, "since Mar 4"))
            self.assertEqual(dashboard.recent_change(h, "Windows", days=1), (8, "last 1 days"))
            self.assertIsNone(dashboard.recent_change(h[:1], "Windows"))

    def test_render_and_snapshot(self):
        now = datetime.datetime(2026, 11, 12, 0, 32, tzinfo=datetime.timezone.utc)
        s = dashboard.summarize(FIXTURE, RULES)
        page = dashboard.render(s, "o/r", now)
        for text in ("Latest version", "Windows downloads", "Linux downloads", "All versions: <b>26</b> downloads (Windows 22 &middot; Linux 4)", "Installer 7", "mystery.<wbr>bin",
                     "6:32 PM CST", "github.com/o/r/releases", "chart appears once snapshots span",
                     "prefers-color-scheme:dark"):
            self.assertIn(text, page)
        self.assertEqual(page.count("<details"), 2)  # one per version
        self.assertEqual(page.count("<details open>"), 1)
        self.assertNotIn("<script", page)
        self.assertNotIn("<rect", page)
        for bad in ("src=", "@import", "url("):
            self.assertNotIn(bad, page)
        with tempfile.TemporaryDirectory() as d:
            self._history(d)
            page = dashboard.render(s, "o/r", now, dashboard.load_history(d, "v1", RULES))
        self.assertIn("<rect", page)
        self.assertIn('class="val"', page)  # number on each bar
        self.assertIn('data-tip="v1.1 Windows: 12"', page)  # hover text on the per-version bars
        self.assertNotIn("chart appears once", page)
        with tempfile.TemporaryDirectory() as d:
            now = datetime.datetime(2026, 3, 4, 5, 6, 7, tzinfo=datetime.timezone.utc)
            path = dashboard.save_snapshot(FIXTURE, "o/r", d, now)
            self.assertTrue(path.endswith("2026-03-04T050607Z.json"))
            with open(path) as f:
                snap = json.load(f)
            self.assertEqual(snap["releases"][1]["assets"][0]["download_count"], 5)

    def test_sizes_and_names(self):
        self.assertEqual(dashboard.mb(0), "0 KB")
        self.assertEqual(dashboard.mb(209), "0 KB")
        self.assertEqual(dashboard.mb(4096), "4 KB")
        self.assertEqual(dashboard.mb(1048575), "1,024 KB")
        self.assertEqual(dashboard.mb(1048576), "1.0 MB")
        self.assertEqual(dashboard.mb(688655610), "656.8 MB")
        self.assertEqual(dashboard.break_name("FrightWorks-win-Setup.exe"),
                         "FrightWorks-<wbr>win-<wbr>Setup.<wbr>exe")
        self.assertEqual(dashboard.break_name("a<b>.zip"), "a&lt;b&gt;.<wbr>zip")

    def test_markdown(self):
        md = dashboard.render_markdown(dashboard.summarize(FIXTURE, RULES), "o/r", "2026-03-04 05:06 UTC")
        self.assertIn("Current version **v1.1**", md)
        self.assertIn("Last updated 2026-03-04 05:06 UTC", md)
        self.assertIn("| v1.1 | 2026-02-01 | 12 | 1 | 20 | 4 | 2 | 8 |", md)
        self.assertIn("| v1.0 | 2026-01-01 | 10 | 0 | 0 | 0 | 0 | 0 |", md)
        self.assertIn("| mystery.bin | unclassified | 100 B | 3 |", md)
        self.assertNotIn("\r", md)


if __name__ == "__main__":
    unittest.main()
