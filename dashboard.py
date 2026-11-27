#!/usr/bin/env python3
"""Collect download counts for a GitHub repo's releases: a snapshot in data/, stats.json and STATS.md.

The FrightWorks web server runs this every 5 minutes; frightworks.dev/stats/ draws the page from its stats.json.
Standard library only. Usage: python dashboard.py [--repo OWNER/NAME] [--out PATH]
"""
import argparse
import datetime
import fnmatch
import html
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REPO = "FrightWorks/FrightWorks-no100f"
UNCLASSIFIED = "unclassified"


def fetch_releases(repo, cache=None):
    """All releases with assets: via gh if present (and no GITHUB_TOKEN is set), else the REST API.

    With `cache` (a file path, REST API only), the first page is requested conditionally with the ETag of the last
    answer. GitHub answers 304 Not Modified when nothing changed, and an authorized 304 doesn't count against the
    rate limit; the releases then come from the cache. Only a single-page answer is cached.
    """
    token = os.environ.get("GITHUB_TOKEN")
    if shutil.which("gh") and not token and not cache:
        out = subprocess.run(
            ["gh", "api", "--paginate", "--slurp", f"repos/{repo}/releases?per_page=100"],
            capture_output=True, text=True, check=True).stdout
        return [r for page in json.loads(out) for r in page]
    cached = None
    if cache:
        try:
            with open(cache, encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("repo") != repo or not cached.get("etag"):
                cached = None
        except (OSError, ValueError):
            cached = None
    releases, page = [], 1
    while True:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/releases?per_page=100&page={page}",
            headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                     "User-Agent": "frightworks-download-stats"})
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        if page == 1 and cached:
            req.add_header("If-None-Match", cached["etag"])
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                batch = json.load(resp)
                etag = resp.headers.get("ETag")
        except urllib.error.HTTPError as e:
            if e.code == 304 and page == 1 and cached:
                return cached["releases"]
            raise
        releases += batch
        if len(batch) < 100:
            if cache and page == 1 and etag:
                write_atomic(cache, json.dumps({"repo": repo, "etag": etag, "releases": batch}))
            return releases
        page += 1


def write_atomic(path, text):
    """Write `text` to `path` via a temporary file and a rename, so readers never see half a file."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def load_rules(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)["rules"]


def classify(name, rules):
    """Return (type, description) of the first matching rule, else unclassified."""
    for rule in rules:
        if fnmatch.fnmatchcase(name.lower(), rule["pattern"].lower()):
            return rule["type"], rule["description"]
    return UNCLASSIFIED, UNCLASSIFIED


def summarize(releases, rules):
    """Group raw API releases into versions (newest first), type rollups and a grand total."""
    versions = []
    rollup = {}
    for rel in releases:
        files = []
        for a in rel.get("assets", []):
            ftype, desc = classify(a["name"], rules)
            files.append({"name": a["name"], "downloads": a["download_count"],
                          "size": a["size"], "type": ftype, "description": desc})
            r = rollup.setdefault(ftype, {"downloads": 0, "files": 0})
            r["downloads"] += a["download_count"]
            r["files"] += 1
        files.sort(key=lambda f: (-f["downloads"], f["name"]))
        versions.append({
            "tag": rel["tag_name"],
            "date": (rel.get("published_at") or rel.get("created_at") or "")[:10],
            "draft": bool(rel.get("draft")), "prerelease": bool(rel.get("prerelease")),
            "downloads": sum(f["downloads"] for f in files), "files": files})
    versions.sort(key=lambda v: v["date"], reverse=True)
    return {"total": sum(v["downloads"] for v in versions), "versions": versions,
            "types": sorted(({"type": k, **v} for k, v in rollup.items()),
                            key=lambda t: (-t["downloads"], t["type"]))}


def human_size(n):
    if n < 1024:
        return f"{n} B"
    for unit in ("KB", "MB", "GB"):
        n /= 1024
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"


# type -> (platform, kind). "player" = a person getting the game; "update" = update packages the
# in-app updater fetches. Update feeds (RELEASES, *.json) are update *checks*, so they are in neither.
TYPE_KINDS = {
    "Windows installer": ("Windows", "player"), "Windows zip": ("Windows", "player"),
    "Windows update package": ("Windows", "update"),
    "Linux AppImage": ("Linux", "player"), "Linux tarball": ("Linux", "player"),
    "Linux update package": ("Linux", "update"),
}
PLAYER_LABELS = {"Windows installer": "Installer", "Windows zip": "Zip", "Linux AppImage": "AppImage",
                 "Linux tarball": "Tarball"}
PLATFORMS = ("Windows", "Linux")
VERSION_LIMIT = 5  # how many versions the page and STATS.md show


def current_version(versions):
    """The newest published version: first non-draft, non-pre-release (else first non-draft, else first)."""
    for ok in (lambda v: not v["draft"] and not v["prerelease"], lambda v: not v["draft"], lambda v: True):
        for v in versions:
            if ok(v):
                return v
    return None


FEED_PLATFORMS = {"Windows update feed": "Windows", "Linux update feed": "Linux"}


def shown_versions(versions):
    """The newest VERSION_LIMIT published (non-draft) versions, current one first."""
    pub = [v for v in versions if not v["draft"]] or list(versions)
    cur = current_version(pub)
    rest = [v for v in pub if v is not cur]
    return ([cur] if cur else []) + rest[:VERSION_LIMIT - 1]


def platform_stats(version):
    """Per-platform numbers for one version.

    {platform: {"downloads", "parts": [(label, n)], "updates", "update_parts": [(label, n)], "checks"}}
    downloads = installer + zip (Windows) or AppImage + tarball (Linux) combined. updates = downloads of this version's own
    update packages (a release also carries the previous version's full package; that one is skipped).
    checks = the busiest update-feed file, i.e. roughly how often installed copies looked for an update.
    """
    out = {p: {"downloads": 0, "parts": {}, "updates": 0, "update_parts": {}, "checks": 0} for p in PLATFORMS}
    if not version:
        return out
    ver = version["tag"].lstrip("vV")
    for f in version["files"]:
        if f["type"] in FEED_PLATFORMS:
            st = out[FEED_PLATFORMS[f["type"]]]
            st["checks"] = max(st["checks"], f["downloads"])
            continue
        plat, kind = TYPE_KINDS.get(f["type"], (None, None))
        if not plat:
            continue
        st = out[plat]
        if kind == "player":
            st["downloads"] += f["downloads"]
            label, bucket = PLAYER_LABELS[f["type"]], st["parts"]
        else:
            if f"-{ver}-" not in f["name"]:
                continue
            st["updates"] += f["downloads"]
            label, bucket = ("Delta" if "delta" in f["name"].lower() else "Full"), st["update_parts"]
        bucket[label] = bucket.get(label, 0) + f["downloads"]
    for st in out.values():
        for key in ("parts", "update_parts"):
            st[key] = sorted(st[key].items(), key=lambda kv: (-kv[1], kv[0]))
    return out


def central_time(utc):
    """Convert an aware UTC datetime to (naive US Central datetime, 'CST'|'CDT') without tz databases."""
    def nth_sunday(year, month, n):
        d = datetime.datetime(year, month, 1)
        return d + datetime.timedelta(days=(6 - d.weekday()) % 7 + 7 * (n - 1))
    naive = utc.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    start = nth_sunday(naive.year, 3, 2) + datetime.timedelta(hours=8)   # 2:00 CST = 08:00 UTC
    end = nth_sunday(naive.year, 11, 1) + datetime.timedelta(hours=7)    # 2:00 CDT = 07:00 UTC
    if start <= naive < end:
        return naive - datetime.timedelta(hours=5), "CDT"
    return naive - datetime.timedelta(hours=6), "CST"


def parse_utc(s):
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)


def load_history(data_dir, tag, rules):
    """[(taken_at ISO string, platform_stats of release `tag`)] from every snapshot, oldest first."""
    points = []
    if os.path.isdir(data_dir):
        for name in sorted(os.listdir(data_dir)):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(data_dir, name), encoding="utf-8") as f:
                    snap = json.load(f)
                rels = [r for r in snap["releases"] if r["tag"] == tag]
                if not rels:
                    continue
                files = [{"name": a["name"], "downloads": a["download_count"], "type": classify(a["name"], rules)[0]}
                         for r in rels for a in r["assets"]]
                parse_utc(snap["taken_at"])
                points.append((snap["taken_at"], platform_stats({"tag": tag, "files": files})))
            except (OSError, ValueError, KeyError, TypeError):
                continue
    points.sort(key=lambda p: p[0])
    return points


def recent_change(history, platform, days=7):
    """(new player downloads, label) over the last `days`, or None with fewer than two snapshots.

    Uses the newest snapshot at least `days` old (but not more than a day older than that) as the baseline;
    otherwise the oldest snapshot, and the label says so.
    """
    if len(history) < 2:
        return None
    newest_t = parse_utc(history[-1][0])
    cutoff = newest_t - datetime.timedelta(days=days)
    older = [h for h in history if cutoff - datetime.timedelta(days=1) <= parse_utc(h[0]) <= cutoff]
    base = older[-1] if older else history[0]
    label = f"last {days} days" if older else f"since {central_time(parse_utc(base[0]))[0]:%b} {central_time(parse_utc(base[0]))[0].day}"
    return max(history[-1][1][platform]["downloads"] - base[1][platform]["downloads"], 0), label


def daily_new(history):
    """[(date, new player downloads that day, Central time)] from consecutive days' last snapshots.
    Days after a gap in the snapshots are skipped."""
    last = {}
    for t, st in history:
        last[central_time(parse_utc(t))[0].date()] = sum(st[p]["downloads"] for p in PLATFORMS)
    days = sorted(last)
    return [(d, max(last[d] - last[prev], 0)) for prev, d in zip(days, days[1:])
            if (d - prev).days == 1]  # a gap in snapshots would otherwise show as one huge day


DAILY_DAYS = 14  # most recent days of new downloads in stats.json


def all_versions_total(versions):
    """(total, {platform: downloads}) of installer/zip/AppImage/tarball downloads over every published version."""
    per = {p: 0 for p in PLATFORMS}
    for v in versions:
        if not v["draft"]:
            for p, st in platform_stats(v).items():
                per[p] += st["downloads"]
    return sum(per.values()), per


SITE_STATS_URL = "https://frightworks.dev/stats/"


def render_redirect(url=SITE_STATS_URL):
    """The Pages index: sends visitors on to the stats page on frightworks.dev."""
    u = html.escape(url)
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>FrightWorks download stats</title><link rel="canonical" href="{u}">'
            f'<meta http-equiv="refresh" content="0; url={u}"></head>'
            f'<body><p>The download stats are now at <a href="{u}">{u}</a>.</p></body></html>\n')


DATA_SCHEMA = 1  # bump when a field in stats.json changes meaning or is removed


def render_data(summary, repo, generated, history=None):
    """The numbers the page shows, as a dict for stats.json (read by frightworks.dev/stats/ at build time).

    Same rules as the page: the last VERSION_LIMIT published versions (current first), per-platform numbers,
    the all-versions total, the 7-day change and the last DAILY_DAYS days of new downloads for the current version.
    """
    history = history or []
    shown = shown_versions(summary["versions"])
    all_total, all_per = all_versions_total(summary["versions"])

    def platforms(st):
        return {p: {"downloads": st[p]["downloads"], "updates": st[p]["updates"], "checks": st[p]["checks"],
                    "parts": [{"label": l, "count": n} for l, n in st[p]["parts"]],
                    "updateParts": [{"label": l, "count": n} for l, n in st[p]["update_parts"]]}
                for p in PLATFORMS}

    versions = []
    for v in shown:
        st = platform_stats(v)
        versions.append({"tag": v["tag"], "date": v["date"], "prerelease": v["prerelease"],
                         "downloads": sum(st[p]["downloads"] for p in PLATFORMS), "platforms": platforms(st),
                         "files": [{"name": f["name"], "description": f["description"], "size": f["size"],
                                    "downloads": f["downloads"]} for f in v["files"]]})
    recent = {}
    for p in PLATFORMS:
        r = recent_change(history, p) if shown else None
        recent[p] = {"count": r[0], "label": r[1]} if r else None
    daily = [{"date": d.isoformat(), "count": n} for d, n in daily_new(history)[-DAILY_DAYS:]] if shown else []
    gen = generated.strftime("%Y-%m-%dT%H:%M:%SZ") if isinstance(generated, datetime.datetime) else str(generated)
    return {"schema": DATA_SCHEMA, "repo": repo, "releasesUrl": f"https://github.com/{repo}/releases",
            "generated": gen, "allVersions": {"total": all_total, "platforms": all_per},
            "recent": recent, "versions": versions, "daily": daily}


def render_markdown(summary, repo, generated):
    """The last few versions' numbers as GitHub-flavoured markdown."""
    def cell(s):
        return str(s).replace("|", "\\|")
    shown = shown_versions(summary["versions"])
    out = [f"# Download stats: {repo}", ""]
    if not shown:
        return "\n".join(out + ["No releases yet.", ""])
    cur = shown[0]
    out += [f"Current version **{cur['tag']}** (released {cur['date']}). Last updated {generated}.", "",
            "Downloads = installer + zip (Windows) or AppImage + tarball (Linux); GitHub counts requests, not unique people. "
            "Update files = a version's update packages fetched by the in-app updater. Update checks = the "
            "busiest update-feed file, a rough sign of installs in use.", "",
            f"## Last {len(shown)} versions", "",
            "| Version | Released | Windows downloads | Windows update files | Windows update checks "
            "| Linux downloads | Linux update files | Linux update checks |", "|---|---|---:|---:|---:|---:|---:|---:|"]
    for v in shown:
        st = platform_stats(v)
        cols = " | ".join(f"{st[p][k]:,}" for p in PLATFORMS for k in ("downloads", "updates", "checks"))
        out.append(f"| {v['tag']} | {v['date']} | {cols} |")
    out += ["", f"## Files in {cur['tag']}", "", "| File | What it is | Size | Downloads |", "|---|---|---:|---:|"]
    for f in cur["files"]:
        out.append(f"| {cell(f['name'])} | {cell(f['description'])} | {human_size(f['size'])} | {f['downloads']:,} |")
    out.append("")
    return "\n".join(out)


def save_snapshot(releases, repo, data_dir, now):
    """Write the raw per-asset counts to data/<UTC timestamp>.json."""
    snap = {"repo": repo, "taken_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "releases": [{"tag": r["tag_name"], "published_at": r.get("published_at"),
                          "assets": [{"name": a["name"], "size": a["size"],
                                      "download_count": a["download_count"]}
                                     for a in r.get("assets", [])]} for r in releases]}
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, now.strftime("%Y-%m-%dT%H%M%SZ") + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snap, f, indent=1)
    return path


SNAPSHOT_FORMAT = "%Y-%m-%dT%H%M%SZ.json"
KEEP_ALL = datetime.timedelta(days=2)
KEEP_HOURLY = datetime.timedelta(days=30)


def prune_snapshots(data_dir, now):
    """Thin out frequent snapshots; returns the file names removed.

    Keeps every snapshot from the last KEEP_ALL, then the last one of each UTC hour up to KEEP_HOURLY old, then
    the last one of each US Central day (what daily_new and recent_change need). The newest file always stays.
    Files whose name isn't a snapshot timestamp are left alone.
    """
    snaps = []
    for name in os.listdir(data_dir) if os.path.isdir(data_dir) else []:
        try:
            t = datetime.datetime.strptime(name, SNAPSHOT_FORMAT).replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            continue
        snaps.append((t, name))
    snaps.sort()
    keep_bucket = {}  # bucket -> newest name in it
    for t, name in snaps:
        age = now - t
        if age <= KEEP_ALL:
            bucket = name
        elif age <= KEEP_HOURLY:
            bucket = ("hour", t.strftime("%Y-%m-%dT%H"))
        else:
            bucket = ("day", central_time(t)[0].date().isoformat())
        keep_bucket[bucket] = name  # sorted, so the last one wins
    keep = set(keep_bucket.values())
    if snaps:
        keep.add(snaps[-1][1])
    removed = [name for _, name in snaps if name not in keep]
    for name in removed:
        os.remove(os.path.join(data_dir, name))
    return removed


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--map", default=os.path.join(HERE, "asset-types.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "output", "index.html"),
                    help="redirect page path; empty to skip it (then --json is required)")
    ap.add_argument("--md", default=os.path.join(HERE, "STATS.md"), help="markdown table path; empty to skip it")
    ap.add_argument("--json", default=None, help="stats.json path (default: next to --out)")
    ap.add_argument("--data-dir", default=os.path.join(HERE, "data"))
    ap.add_argument("--cache", default=None, help="file for the last API answer and its ETag (conditional requests)")
    ap.add_argument("--prune", action="store_true", help="thin out old snapshots (see prune_snapshots)")
    args = ap.parse_args()
    if not args.out and not args.json:
        ap.error("--json is required when --out is empty")
    now = datetime.datetime.now(datetime.timezone.utc)
    releases = fetch_releases(args.repo, args.cache)
    snap = save_snapshot(releases, args.repo, args.data_dir, now)
    if args.prune:
        removed = prune_snapshots(args.data_dir, now)
        if removed:
            print(f"pruned {len(removed)} old snapshot(s)")
    summary = summarize(releases, load_rules(args.map))
    cur = current_version(summary["versions"])
    history = load_history(args.data_dir, cur["tag"], load_rules(args.map)) if cur else []
    if args.out:
        write_atomic(args.out, render_redirect())
    json_path = args.json or os.path.join(os.path.dirname(os.path.abspath(args.out)), "stats.json")
    write_atomic(json_path, json.dumps(render_data(summary, args.repo, now, history), indent=1))
    if args.md:
        write_atomic(args.md, render_markdown(summary, args.repo, now.strftime("%Y-%m-%d %H:%M UTC")))
    print(f"snapshot: {snap}\nredirect: {args.out or '-'}\ndata: {json_path}\nmarkdown: {args.md or '-'}\n"
          f"total downloads: {summary['total']}")
    for t in summary["types"]:
        print(f"  {t['type']}: {t['downloads']}")
    unk = sorted({f["name"] for v in summary["versions"] for f in v["files"] if f["type"] == UNCLASSIFIED})
    if unk:
        print("unclassified:", ", ".join(unk), file=sys.stderr)


if __name__ == "__main__":
    main()
