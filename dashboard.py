#!/usr/bin/env python3
"""Build a static download-count dashboard for a GitHub repo's releases.

Standard library only. Usage: python dashboard.py [--repo OWNER/NAME] [--out PATH]
"""
import argparse
import datetime
import fnmatch
import html
import json
import re
import os
import shutil
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REPO = "FrightWorks/FrightWorks-no100f"
UNCLASSIFIED = "unclassified"


def fetch_releases(repo):
    """All releases with assets: via gh if present, else the REST API (GITHUB_TOKEN optional)."""
    if shutil.which("gh"):
        out = subprocess.run(
            ["gh", "api", "--paginate", "--slurp", f"repos/{repo}/releases?per_page=100"],
            capture_output=True, text=True, check=True).stdout
        return [r for page in json.loads(out) for r in page]
    releases, page = [], 1
    while True:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/releases?per_page=100&page={page}",
            headers={"Accept": "application/vnd.github+json"})
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req) as resp:
            batch = json.load(resp)
        releases += batch
        if len(batch) < 100:
            return releases
        page += 1


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
    "Linux AppImage": ("Linux", "player"), "Linux update package": ("Linux", "update"),
}
PLAYER_LABELS = {"Windows installer": "Installer", "Windows zip": "Zip", "Linux AppImage": "AppImage"}
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
    downloads = installer + zip (Windows) or AppImage (Linux) combined. updates = downloads of this version's own
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


def format_central(utc):
    local, zone = central_time(utc)
    ampm = "AM" if local.hour < 12 else "PM"
    return f"{local:%b} {local.day}, {local.year}, {local.hour % 12 or 12}:{local:%M} {ampm} {zone}"


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


def mb(n):
    """Size for display: KB below 1 MB, else MB with one decimal."""
    if n < 1048576:
        return f"{round(n / 1024):,} KB"
    return f"{n / 1048576:,.1f} MB"


def break_name(name):
    """Escape a file name and allow line breaks only after '-' and '.'."""
    return "<wbr>".join(html.escape(p) for p in re.split(r"(?<=[-.])", name))


CSS = """
:root{color-scheme:light dark;--bg:#f4f4f2;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#6b6a66;
--line:#e3e2de;--s1:#2a78d6;--s2:#eb6834;--s3:#8a8985;--track:#ecebe7;--link:#1c5fb0}
@media(prefers-color-scheme:dark){:root{--bg:#121211;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--muted:#a3a299;
--line:#333330;--s1:#3987e5;--s2:#d95926;--s3:#8a8985;--track:#2b2b29;--link:#7db3f5}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:60rem;margin:0 auto;padding:1rem}
header{padding:1.5rem 0 .5rem}h1{margin:0;font-size:1.7rem;line-height:1.2}
.sub{color:var(--ink2);margin:.3rem 0}a{color:var(--link)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(11rem,1fr));gap:.75rem;margin:1rem 0}
.tile,section{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:1rem}
section{margin:1rem 0}h2{margin:0 0 .25rem;font-size:1.2rem}.note{color:var(--ink2);font-size:.9rem;margin:.1rem 0 .8rem}
.tile .k{color:var(--ink2);font-size:.85rem}
.tile .v{font-size:2rem;font-weight:700;line-height:1.2;font-variant-numeric:tabular-nums}
.tile .d{color:var(--muted);font-size:.8rem}.tile .new{color:var(--s1);font-weight:600}
.legend{display:flex;gap:1rem;flex-wrap:wrap;font-size:.85rem;color:var(--ink2);margin-bottom:.6rem}
.sw{display:inline-block;width:.8rem;height:.8rem;border-radius:3px;margin-right:.35rem;vertical-align:-1px}
.s1{background:var(--s1)}.s2{background:var(--s2)}
.row{display:grid;grid-template-columns:5.5rem 1fr 4.5rem;gap:.6rem;align-items:center;margin:.35rem 0;font-size:.9rem}
.row .n,td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.bar{display:flex;gap:2px;height:1.2rem}
.bar i{display:block;height:100%;border-radius:0 4px 4px 0;min-width:2px}
td small{display:block;color:var(--muted);font-size:.8rem}
.trend.bars rect{fill:var(--s1)}
details{border-top:1px solid var(--line);padding:.5rem 0}details:first-of-type{border-top:0}
summary{cursor:pointer;font-weight:600;display:flex;flex-wrap:wrap;gap:.25rem .8rem;align-items:baseline}
summary small{font-weight:400;color:var(--ink2)}
.tbl{overflow-x:auto}table{border-collapse:collapse;width:100%;margin:.5rem 0;font-size:.9rem}
th,td{text-align:left;padding:.4rem .5rem;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--ink2);font-weight:600;font-size:.8rem}.desc{color:var(--ink2);min-width:12rem}.fn{min-width:9rem}td.n{white-space:nowrap}
.trend{width:100%;height:auto}.trend text{fill:var(--muted);font-size:11px}
.trend .ln{fill:none;stroke:var(--s1);stroke-width:2}.trend .gr{stroke:var(--line);stroke-width:1}
.trend circle{fill:var(--s1);stroke:var(--surface);stroke-width:2}
footer{color:var(--muted);font-size:.8rem;text-align:center;padding:1rem 0}
@media(max-width:34rem){.row{grid-template-columns:4rem 1fr 3.5rem}h1{font-size:1.4rem}.tile .v{font-size:1.6rem}}
"""


def render_daily(daily):
    if not daily:
        return ('<p class="note">The chart appears once snapshots span at least two days '
                "(it shows new downloads per day).</p>")
    W, H, L, R, T, B = 600, 220, 40, 14, 14, 30
    mx = max([n for _, n in daily] + [1])
    slot = (W - L - R) / len(daily)
    bw = min(slot * 0.7, 40)
    bars = []
    for i, (d, n) in enumerate(daily):
        h = n / mx * (H - T - B)
        x = L + i * slot + (slot - bw) / 2
        bars.append(f'<rect x="{x:.1f}" y="{H - B - h:.1f}" width="{bw:.1f}" height="{max(h, 1):.1f}" rx="3">'
                    f'<title>{d:%b} {d.day}: {n:,} new</title></rect>')
    return (f'<svg class="trend bars" viewBox="0 0 {W} {H}" role="img" aria-label="New downloads per day, '
            f'{daily[0][0]:%b} {daily[0][0].day} to {daily[-1][0]:%b} {daily[-1][0].day}">'
            f'<line class="gr" x1="{L}" x2="{W - R}" y1="{H - B}" y2="{H - B}"/>'
            f'<text x="{L - 6}" y="{T + 4}" text-anchor="end">{mx:,}</text>'
            f'<text x="{L - 6}" y="{H - B + 4}" text-anchor="end">0</text>'
            f'<text x="{L}" y="{H - 8}">{daily[0][0]:%b} {daily[0][0].day}</text>'
            f'<text x="{W - R}" y="{H - 8}" text-anchor="end">{daily[-1][0]:%b} {daily[-1][0].day}</text>'
            + "".join(bars) + "</svg>")


def parts_text(parts):
    return " &middot; ".join(f"{html.escape(l)} {n:,}" for l, n in parts)


def render_chart(versions):
    """One row per version: bar of downloads, split Windows / Linux, update files shown at the right."""
    stats = [platform_stats(v) for v in versions]
    totals = [sum(st[p]["downloads"] for p in PLATFORMS) for st in stats]
    mx = max(totals + [1])
    legend = ('<div class="legend"><span><span class="sw s1"></span>Windows downloads</span>'
              '<span><span class="sw s2"></span>Linux downloads</span></div>')
    rows = []
    for v, st, total in zip(versions, stats, totals):
        segs = "".join(f'<i class="{c}" style="width:{st[p]["downloads"] / mx * 100:.2f}%" '
                       f'title="{p}: {st[p]["downloads"]:,}"></i>'
                       for p, c in zip(PLATFORMS, ("s1", "s2")) if st[p]["downloads"])
        rows.append(f'<div class="row"><span>{html.escape(v["tag"])}</span><div class="bar">{segs}</div>'
                    f'<span class="n">{total:,}</span></div>')
    return legend + "".join(rows)


def render_version_table(st):
    out = ['<table><tr><th>Platform</th><th class="n">Downloads</th><th class="n">Update files</th>'
           '<th class="n">Update checks</th></tr>']
    for p in PLATFORMS:
        x = st[p]
        out.append(f'<tr><td>{p}</td><td class="n">{x["downloads"]:,}<small>{parts_text(x["parts"])}</small></td>'
                   f'<td class="n">{x["updates"]:,}<small>{parts_text(x["update_parts"])}</small></td>'
                   f'<td class="n">{x["checks"]:,}</td></tr>')
    out.append("</table>")
    return "".join(out)


def render_files(v):
    e = html.escape
    out = ['<table><tr><th>File</th><th>What it is</th><th class="n">Size</th><th class="n">Downloads</th></tr>']
    for f in v["files"]:
        out.append(f'<tr><td class="fn">{break_name(f["name"])}</td><td class="desc">{e(f["description"])}</td>'
                   f'<td class="n">{mb(f["size"])}</td><td class="n">{f["downloads"]:,}</td></tr>')
    out.append("</table>")
    return "".join(out)


def all_versions_total(versions):
    """(total, {platform: downloads}) of installer/zip/AppImage downloads over every published version."""
    per = {p: 0 for p in PLATFORMS}
    for v in versions:
        if not v["draft"]:
            for p, st in platform_stats(v).items():
                per[p] += st["downloads"]
    return sum(per.values()), per


def render(summary, repo, generated, history=None):
    """The self-contained page. `generated` is an aware UTC datetime (or a plain string)."""
    e = html.escape
    updated = format_central(generated) if isinstance(generated, datetime.datetime) else str(generated)
    shown = shown_versions(summary["versions"])
    cur = shown[0] if shown else None
    history = history or []
    all_total, all_per = all_versions_total(summary["versions"])
    repo_url = f"https://github.com/{repo}/releases"
    tile = lambda k, v, d="": (f'<div class="tile"><div class="k">{k}</div><div class="v">{v}</div>'
                               f'<div class="d">{d}</div></div>')
    out = [f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FrightWorks PC port downloads</title><style>{CSS}</style></head><body><main>
<header><h1>FrightWorks PC port downloads</h1>
<p class="sub">Last updated {e(updated)} &middot; <a href="{e(repo_url)}">See the releases on GitHub</a></p>
<p class="sub total">All versions: <b>{all_total:,}</b> downloads ({" &middot; ".join(f"{p} {n:,}" for p, n in all_per.items())})</p></header>"""]
    if cur:
        stats = platform_stats(cur)
        out.append('<div class="tiles">')
        out.append(tile("Latest version", e(cur["tag"]), f"released {e(cur['date'])}"))
        for p in PLATFORMS:
            recent = recent_change(history, p)
            extra = f'<br><span class="new">+{recent[0]:,} {recent[1]}</span>' if recent else ""
            out.append(tile(f"{p} downloads", f"{stats[p]['downloads']:,}",
                            (parts_text(stats[p]["parts"]) or "no downloads yet") + extra))
        up = {p: stats[p]["updates"] for p in PLATFORMS}
        out.append(tile("Update files", f"{sum(up.values()):,}", " &middot; ".join(f"{p} {n:,}" for p, n in up.items())))
        out.append("</div>")
        out.append(f'<section><h2>Downloads per version</h2><p class="note">The last {len(shown)} versions, newest first. '
                   "Installer and zip are combined for Windows. Counts keep growing after a release, so older "
                   "versions are not final.</p>" + render_chart(shown) + "</section>")
        out.append('<section><h2>Details per version</h2><p class="note">Click a version to see its numbers and files.</p>')
        for v in shown:
            flag = " (pre-release)" if v["prerelease"] else ""
            st = platform_stats(v)
            total = sum(st[p]["downloads"] for p in PLATFORMS)
            out.append(f'<details{" open" if v is cur else ""}><summary><span>{e(v["tag"])}{flag}</span>'
                       f'<small>{e(v["date"])} &middot; {total:,} downloads</small></summary><div class="tbl">'
                       + render_version_table(st) + render_files(v) + "</div></details>")
        out.append("</section>")
        out.append('<section><h2>What these numbers mean</h2><p class="note"><b>Downloads</b> are requests for the '
                   "game: the Windows installer plus the Windows zip, or the Linux AppImage. "
                   "GitHub counts requests, not people, so retries, scanners and someone grabbing both the installer "
                   "and the zip all add up; treat it as an upper bound on new players.</p>"
                   '<p class="note"><b>Update files</b> are the in-app updater fetching a version\'s update packages '
                   "(full or delta). One update can fetch more than one file, so this is not the number of people who updated.</p>"
                   '<p class="note"><b>Update checks</b> are installed copies looking for an update (the busiest feed file), '
                   "a rough sign of installs in use, not unique users. The same installs can show up under several "
                   "versions, so don't add update checks across rows.</p></section>")
        out.append(f"<section><h2>New downloads per day</h2><p class=\"note\">Installer, zip and AppImage for "
                   f"{e(cur['tag'])}, from the twice-daily snapshots (days are Central time).</p>"
                   + render_daily(daily_new(history)) + "</section>")
    else:
        out.append('<section><p class="note">No releases yet.</p></section>')
    out.append("<footer>One snapshot per run, twice a day.</footer></main></body></html>\n")
    return "\n".join(out)


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
            "Downloads = installer + zip (Windows) or AppImage (Linux); GitHub counts requests, not unique people. "
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


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--map", default=os.path.join(HERE, "asset-types.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "output", "index.html"))
    ap.add_argument("--md", default=os.path.join(HERE, "STATS.md"))
    ap.add_argument("--data-dir", default=os.path.join(HERE, "data"))
    args = ap.parse_args()
    now = datetime.datetime.now(datetime.timezone.utc)
    releases = fetch_releases(args.repo)
    snap = save_snapshot(releases, args.repo, args.data_dir, now)
    summary = summarize(releases, load_rules(args.map))
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        cur = current_version(summary["versions"])
        history = load_history(args.data_dir, cur["tag"], load_rules(args.map)) if cur else []
        f.write(render(summary, args.repo, now, history))
    with open(args.md, "w", encoding="utf-8", newline="\n") as f:
        f.write(render_markdown(summary, args.repo, now.strftime("%Y-%m-%d %H:%M UTC")))
    print(f"snapshot: {snap}\npage: {args.out}\nmarkdown: {args.md}\ntotal downloads: {summary['total']}")
    for t in summary["types"]:
        print(f"  {t['type']}: {t['downloads']}")
    unk = sorted({f["name"] for v in summary["versions"] for f in v["files"] if f["type"] == UNCLASSIFIED})
    if unk:
        print("unclassified:", ", ".join(unk), file=sys.stderr)


if __name__ == "__main__":
    main()
