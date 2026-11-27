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
        self.assertEqual(c("FrightWorks-1.3.0-Windows-x64.zip"), "Windows zip")
        self.assertEqual(c("FrightWorks-1.3.0-Linux-x64.tar.gz"), "Linux tarball")
        self.assertEqual(dashboard.TYPE_KINDS["Linux tarball"], ("Linux", "player"))
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
        data = dashboard.render_data(dashboard.summarize(rels, RULES), "o/r", "now")
        self.assertEqual([v["tag"] for v in data["versions"]], ["v8", "v7", "v6", "v5", "v4"])

    def test_central_time(self):
        utc = datetime.timezone.utc
        self.assertEqual(dashboard.central_time(datetime.datetime(2026, 11, 12, 0, 32, tzinfo=utc)),
                         (datetime.datetime(2026, 11, 11, 18, 32), "CST"))
        self.assertEqual(dashboard.central_time(datetime.datetime(2026, 7, 1, 17, 5, tzinfo=utc)),
                         (datetime.datetime(2026, 7, 1, 12, 5), "CDT"))
        # DST ends Sun 2026-11-01 at 07:00 UTC
        self.assertEqual(dashboard.central_time(datetime.datetime(2026, 11, 1, 6, 59, tzinfo=utc))[1], "CDT")
        self.assertEqual(dashboard.central_time(datetime.datetime(2026, 11, 1, 7, 0, tzinfo=utc))[1], "CST")
        # DST starts Sun 2026-03-08 at 08:00 UTC
        self.assertEqual(dashboard.central_time(datetime.datetime(2026, 3, 8, 8, 0, tzinfo=utc))[1], "CDT")

    def _history(self, d):
        for stamp, zip_n, linux_n in (("2026-03-05T200000Z", 9, 3), ("2026-03-04T200000Z", 4, 1),
                                      ("2026-03-04T080000Z", 2, 1), ("2026-03-06T060000Z", 12, 5)):
            now = datetime.datetime.strptime(stamp, "%Y-%m-%dT%H%M%SZ").replace(tzinfo=datetime.timezone.utc)
            rels = [{"tag_name": "v1", "published_at": "2026-03-05T18:00:00Z",
                     "assets": [asset("FrightWorks-1.zip", zip_n), asset("RELEASES", 1),
                                asset("FrightWorks.AppImage", linux_n)]},
                    {"tag_name": "v0", "published_at": "2026-02-01T00:00:00Z",
                     "assets": [asset("FrightWorks-0.zip", 50 + zip_n)]},
                    {"tag_name": "draft", "published_at": None, "assets": [asset("FrightWorks-2.zip", 99)]}]
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
            # every published release: v1 as above plus v0 (50 + zip each time); the draft is left out
            a = dashboard.load_all_history(d, RULES)
            self.assertEqual([st["Windows"]["downloads"] for _, st in a], [54, 58, 68, 74])
            self.assertEqual(dashboard.daily_new(a), [(datetime.date(2026, 3, 5), 12), (datetime.date(2026, 3, 6), 8)])

    def test_release_days(self):
        s = dashboard.summarize(FIXTURE + [
            {"tag_name": "v1.2", "published_at": "2026-02-02T03:00:00Z", "assets": []},  # Feb 1, 9 pm Central
            {"tag_name": "wip", "draft": True, "published_at": None, "assets": []}], RULES)
        self.assertEqual(dashboard.release_days(s["versions"]),
                         {datetime.date(2025, 12, 31): ["v1.0"], datetime.date(2026, 1, 31): ["v1.1"],
                          datetime.date(2026, 2, 1): ["v1.2"]})

    def test_redirect(self):
        page = dashboard.render_redirect()
        self.assertIn('content="0; url=https://frightworks.dev/stats/"', page)
        self.assertIn('rel="canonical" href="https://frightworks.dev/stats/"', page)
        self.assertNotIn("<script", page)

    def test_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            now = datetime.datetime(2026, 3, 4, 5, 6, 7, tzinfo=datetime.timezone.utc)
            path = dashboard.save_snapshot(FIXTURE, "o/r", d, now)
            self.assertTrue(path.endswith("2026-03-04T050607Z.json"))
            with open(path) as f:
                snap = json.load(f)
            self.assertEqual(snap["releases"][1]["assets"][0]["download_count"], 5)

    def test_data(self):
        now = datetime.datetime(2026, 11, 12, 0, 32, tzinfo=datetime.timezone.utc)
        s = dashboard.summarize(FIXTURE, RULES)
        data = dashboard.render_data(s, "o/r", now)
        json.dumps(data)  # serialisable
        self.assertEqual(data["schema"], 1)
        self.assertEqual(data["generated"], "2026-11-12T00:32:00Z")
        self.assertEqual(data["releasesUrl"], "https://github.com/o/r/releases")
        self.assertEqual(data["allVersions"], {"total": 26, "platforms": {"Windows": 22, "Linux": 4}})
        self.assertEqual([v["tag"] for v in data["versions"]], ["v1.1", "v1.0"])
        cur = data["versions"][0]
        self.assertEqual(cur["downloads"], 16)
        win = cur["platforms"]["Windows"]
        self.assertEqual((win["downloads"], win["updates"], win["checks"]), (12, 1, 20))
        self.assertEqual(win["parts"], [{"label": "Installer", "count": 7}, {"label": "Zip", "count": 5}])
        self.assertEqual(cur["platforms"]["Linux"]["updateParts"], [{"label": "Full", "count": 2}])
        self.assertEqual(cur["files"][0]["name"], "releases.win.json")
        self.assertEqual((cur["files"][0]["size"], cur["files"][0]["downloads"]), (100, 20))
        self.assertEqual(data["recent"], {"Windows": None, "Linux": None})
        self.assertEqual((data["daily"], data["dailyAll"]), ([], []))
        with tempfile.TemporaryDirectory() as d:
            self._history(d)
            s3 = dashboard.summarize(FIXTURE + [{"tag_name": "v1.2", "published_at": "2026-03-05T18:00:00Z",
                                                 "assets": []}], RULES)
            data = dashboard.render_data(s3, "o/r", now, dashboard.load_history(d, "v1", RULES),
                                         dashboard.load_all_history(d, RULES))
        self.assertEqual(data["daily"], [{"date": "2026-03-05", "count": 7}, {"date": "2026-03-06", "count": 5}])
        self.assertEqual(data["dailyAll"], [{"date": "2026-03-05", "count": 12, "releases": ["v1.2"]},
                                            {"date": "2026-03-06", "count": 8, "releases": []}])
        self.assertEqual(data["recent"]["Windows"], {"count": 10, "label": "since Mar 4"})
        self.assertEqual(dashboard.render_data(dashboard.summarize([], RULES), "o/r", now)["versions"], [])

    def test_markdown(self):
        md = dashboard.render_markdown(dashboard.summarize(FIXTURE, RULES), "o/r", "2026-03-04 05:06 UTC")
        self.assertIn("Current version **v1.1**", md)
        self.assertIn("Last updated 2026-03-04 05:06 UTC", md)
        self.assertIn("| v1.1 | 2026-02-01 | 12 | 1 | 20 | 4 | 2 | 8 |", md)
        self.assertIn("| v1.0 | 2026-01-01 | 10 | 0 | 0 | 0 | 0 | 0 |", md)
        self.assertIn("| mystery.bin | unclassified | 100 B | 3 |", md)
        self.assertNotIn("\r", md)

    def test_prune(self):
        utc = datetime.timezone.utc
        now = datetime.datetime(2026, 6, 30, 12, 0, tzinfo=utc)
        times = [
            now - datetime.timedelta(minutes=m) for m in (0, 5, 10)                      # recent: all kept
        ] + [
            datetime.datetime(2026, 6, 20, 9, m, tzinfo=utc) for m in (0, 5, 55)         # hourly: 9:55 kept
        ] + [
            datetime.datetime(2026, 6, 20, 10, 0, tzinfo=utc),                           # next hour: kept
            # Older than 30 days: last of each Central day. May 1 23:00 and May 2 04:00 UTC are both May 1 CDT.
            datetime.datetime(2026, 5, 1, 23, 0, tzinfo=utc),
            datetime.datetime(2026, 5, 2, 4, 0, tzinfo=utc),
            datetime.datetime(2026, 5, 2, 6, 0, tzinfo=utc),                             # May 2 CDT: kept
        ]
        with tempfile.TemporaryDirectory() as d:
            for t in times:
                open(os.path.join(d, t.strftime(dashboard.SNAPSHOT_FORMAT)), "w").close()
            open(os.path.join(d, "notes.json"), "w").close()
            removed = dashboard.prune_snapshots(d, now)
            self.assertEqual(sorted(removed), ["2026-05-01T230000Z.json", "2026-06-20T090000Z.json",
                                               "2026-06-20T090500Z.json"])
            left = sorted(os.listdir(d))
            self.assertIn("notes.json", left)
            self.assertEqual(len(left), len(times) - 3 + 1)
            self.assertEqual(dashboard.prune_snapshots(d, now), [])
            # The newest file stays even when it's old.
        with tempfile.TemporaryDirectory() as d:
            for t in (datetime.datetime(2026, 1, 1, 1, tzinfo=utc), datetime.datetime(2026, 1, 1, 2, tzinfo=utc)):
                open(os.path.join(d, t.strftime(dashboard.SNAPSHOT_FORMAT)), "w").close()
            self.assertEqual(dashboard.prune_snapshots(d, now), ["2026-01-01T010000Z.json"])

    def test_fetch_cache(self):
        import io
        import unittest.mock as mock
        calls = []

        class Resp(io.BytesIO):
            def __init__(self, body, etag):
                super().__init__(json.dumps(body).encode())
                self.headers = {"ETag": etag}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake(req, timeout=None):
            calls.append(dict(req.header_items()))
            if req.get_header("If-none-match") == '"e1"':
                raise dashboard.urllib.error.HTTPError(req.full_url, 304, "Not Modified", {}, None)
            return Resp([{"tag_name": "v1", "assets": []}], '"e1"')

        with tempfile.TemporaryDirectory() as d, mock.patch.object(dashboard.urllib.request, "urlopen", fake), \
                mock.patch.dict(os.environ, {"GITHUB_TOKEN": "t"}):
            cache = os.path.join(d, "cache.json")
            first = dashboard.fetch_releases("o/r", cache)
            second = dashboard.fetch_releases("o/r", cache)
            self.assertEqual(first, second)
            self.assertEqual(first[0]["tag_name"], "v1")
            self.assertNotIn("If-none-match", calls[0])
            self.assertEqual(calls[1]["If-none-match"], '"e1"')
            self.assertEqual(calls[1]["Authorization"], "Bearer t")
            # A cache for another repo isn't used.
            dashboard.fetch_releases("o/other", cache)
            self.assertNotIn("If-none-match", calls[2])


if __name__ == "__main__":
    unittest.main()
