#!/usr/bin/env python3
"""Build a static download-count dashboard for a GitHub repo's releases.

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


PLAYER_TYPES = {"Windows installer", "Windows zip", "Linux AppImage"}
UPDATE_TYPES = {"Windows update package", "Linux update package",
                "Windows update feed", "Linux update feed"}


def split_downloads(files):
    """(player, update, other) download totals for a list of file dicts."""
    player = sum(f["downloads"] for f in files if f["type"] in PLAYER_TYPES)
    update = sum(f["downloads"] for f in files if f["type"] in UPDATE_TYPES)
    return player, update, sum(f["downloads"] for f in files) - player - update


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


def load_trend(data_dir):
    """[(taken_at ISO string, total downloads)] from every snapshot in data_dir, oldest first."""
    points = []
    if os.path.isdir(data_dir):
        for name in sorted(os.listdir(data_dir)):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(data_dir, name), encoding="utf-8") as f:
                    snap = json.load(f)
                total = sum(a["download_count"] for r in snap["releases"] for a in r["assets"])
                parse_utc(snap["taken_at"])
                points.append((snap["taken_at"], total))
            except (OSError, ValueError, KeyError, TypeError):
                continue
    points.sort()
    return points


def mb(n):
    return f"{n / 1048576:,.1f} MB"


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
.tile .k{color:var(--ink2);font-size:.85rem}
.tile .v{font-size:2rem;font-weight:700;line-height:1.2;font-variant-numeric:tabular-nums}
.tile .d{color:var(--muted);font-size:.8rem}
section{margin:1rem 0}h2{margin:0 0 .25rem;font-size:1.2rem}.note{color:var(--ink2);font-size:.9rem;margin:.1rem 0 .8rem}
.legend{display:flex;gap:1rem;flex-wrap:wrap;font-size:.85rem;color:var(--ink2);margin-bottom:.6rem}
.sw{display:inline-block;width:.8rem;height:.8rem;border-radius:3px;margin-right:.35rem;vertical-align:-1px}
.s1{background:var(--s1)}.s2{background:var(--s2)}.s3{background:var(--s3)}
.row{display:grid;grid-template-columns:5.5rem 1fr 4.5rem;gap:.6rem;align-items:center;margin:.35rem 0;font-size:.9rem}
.row .n,td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.bar{display:flex;gap:2px;height:1.2rem}
.bar i{display:block;height:100%;border-radius:0 4px 4px 0;min-width:2px}
details{border-top:1px solid var(--line);padding:.5rem 0}details:first-of-type{border-top:0}
summary{cursor:pointer;font-weight:600;display:flex;flex-wrap:wrap;gap:.25rem .8rem;align-items:baseline}
summary small{font-weight:400;color:var(--ink2)}
.tbl{overflow-x:auto}table{border-collapse:collapse;width:100%;margin:.5rem 0;font-size:.9rem}
th,td{text-align:left;padding:.4rem .5rem;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--ink2);font-weight:600;font-size:.8rem}.desc{color:var(--ink2);min-width:12rem}.fn{overflow-wrap:anywhere;min-width:9rem}
.share{display:block;width:5rem;height:.5rem;margin-top:.45rem;background:var(--track);border-radius:4px;overflow:hidden}
.share i{display:block;height:100%;background:var(--s1)}
.trend{width:100%;height:auto}.trend text{fill:var(--muted);font-size:11px}
.trend .ln{fill:none;stroke:var(--s1);stroke-width:2}.trend .gr{stroke:var(--line);stroke-width:1}
.trend circle{fill:var(--s1);stroke:var(--surface);stroke-width:2}
footer{color:var(--muted);font-size:.8rem;text-align:center;padding:1rem 0}
@media(max-width:34rem){.row{grid-template-columns:4rem 1fr 3.5rem}h1{font-size:1.4rem}.tile .v{font-size:1.6rem}}
"""


def render_chart(versions):
    mx = max([v["downloads"] for v in versions] + [1])
    splits = [split_downloads(v["files"]) for v in versions]
    has_other = any(s[2] for s in splits)
    legend = ('<div class="legend"><span><span class="sw s1"></span>Players</span>'
              '<span><span class="sw s2"></span>Update traffic</span>'
              + ('<span><span class="sw s3"></span>Other</span>' if has_other else "") + "</div>")
    rows = []
    for v, (p, u, o) in zip(versions, splits):
        segs = "".join(f'<i class="{c}" style="width:{n / mx * 100:.2f}%" title="{lbl}: {n:,}"></i>'
                       for n, c, lbl in ((p, "s1", "Players"), (u, "s2", "Update traffic"), (o, "s3", "Other")) if n)
        rows.append(f'<div class="row"><span>{html.escape(v["tag"])}</span><div class="bar">{segs}</div>'
                    f'<span class="n">{v["downloads"]:,}</span></div>')
    return legend + "".join(rows)


def render_trend(trend):
    if len(trend) < 2:
        return '<p class="note">The trend chart appears after a few runs (it needs at least two snapshots).</p>'
    W, H, L, R, T, B = 600, 220, 60, 14, 14, 30
    ts = [parse_utc(t).timestamp() for t, _ in trend]
    vals = [v for _, v in trend]
    lo, hi = min(vals), max(vals)
    if hi == lo:
        hi, lo = hi + 1, max(lo - 1, 0)
    t0, t1 = ts[0], ts[-1]
    x = lambda t: L + (t - t0) / ((t1 - t0) or 1) * (W - L - R)
    y = lambda v: T + (hi - v) / (hi - lo) * (H - T - B)
    pts = " ".join(f"{x(t):.1f},{y(v):.1f}" for t, v in zip(ts, vals))
    dots = "".join(f'<circle cx="{x(t):.1f}" cy="{y(v):.1f}" r="4"><title>{format_central(parse_utc(s))}: {v:,}</title></circle>'
                   for t, v, (s, _) in zip(ts, vals, trend))
    d0 = central_time(parse_utc(trend[0][0]))[0]
    d1 = central_time(parse_utc(trend[-1][0]))[0]
    return (f'<svg class="trend" viewBox="0 0 {W} {H}" role="img" aria-label="Total downloads over time, '
            f'from {vals[0]:,} to {vals[-1]:,}">'
            f'<line class="gr" x1="{L}" x2="{W - R}" y1="{y(hi):.1f}" y2="{y(hi):.1f}"/>'
            f'<line class="gr" x1="{L}" x2="{W - R}" y1="{y(lo):.1f}" y2="{y(lo):.1f}"/>'
            f'<text x="{L - 6}" y="{y(hi) + 4:.1f}" text-anchor="end">{hi:,}</text>'
            f'<text x="{L - 6}" y="{y(lo) + 4:.1f}" text-anchor="end">{lo:,}</text>'
            f'<text x="{L}" y="{H - 8}">{d0:%b} {d0.day}</text>'
            f'<text x="{W - R}" y="{H - 8}" text-anchor="end">{d1:%b} {d1.day}</text>'
            f'<polyline class="ln" points="{pts}"/>{dots}</svg>')


def render(summary, repo, generated, trend=None):
    """The self-contained page. `generated` is an aware UTC datetime (or a plain string)."""
    e = html.escape
    updated = format_central(generated) if isinstance(generated, datetime.datetime) else str(generated)
    versions = summary["versions"]
    player_total = sum(split_downloads(v["files"])[0] for v in versions)
    latest = next((v for v in versions if not v["draft"]), versions[0] if versions else None)
    repo_url = f"https://github.com/{repo}/releases"
    tile = lambda k, v, d="": (f'<div class="tile"><div class="k">{k}</div><div class="v">{v}</div>'
                               f'<div class="d">{d}</div></div>')
    out = [f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FrightWorks PC port downloads</title><style>{CSS}</style></head><body><main>
<header><h1>FrightWorks PC port downloads</h1>
<p class="sub">Last updated {e(updated)} &middot; <a href="{e(repo_url)}">See the releases on GitHub</a></p></header>
<div class="tiles">"""]
    out.append(tile("Total downloads", f"{summary['total']:,}", "every file, every version"))
    out.append(tile("Player downloads", f"{player_total:,}", "installer, zip and AppImage; not updates"))
    if latest:
        out.append(tile("Latest version", e(latest["tag"]), f"{latest['downloads']:,} downloads"))
    out.append(tile("Releases", f"{len(versions):,}", "published versions"))
    out.append("</div>")
    out.append('<section><h2>Downloads per version</h2><p class="note">Newest first. '
               "Players are people getting the game; update traffic is the in-app updater fetching files.</p>"
               + render_chart(versions) + "</section>")
    out.append('<section><h2>Details per version</h2><p class="note">Click a version to see its files.</p>')
    for v in versions:
        flag = " (draft)" if v["draft"] else " (pre-release)" if v["prerelease"] else ""
        vt = v["downloads"] or 1
        out.append(f'<details{" open" if v is latest else ""}><summary><span>{e(v["tag"])}{flag}</span>'
                   f'<small>{e(v["date"])} &middot; {v["downloads"]:,} downloads</small></summary><div class="tbl">'
                   '<table><tr><th>File</th><th>What it is</th><th class="n">Size</th><th class="n">Downloads</th>'
                   '<th>Share</th></tr>')
        for f in v["files"]:
            share = f["downloads"] / vt * 100
            out.append(f'<tr><td class="fn">{e(f["name"])}</td><td class="desc">{e(f["description"])}</td>'
                       f'<td class="n">{mb(f["size"])}</td><td class="n">{f["downloads"]:,}</td>'
                       f'<td><span class="share" title="{share:.0f}% of this version">'
                       f'<i style="width:{share:.1f}%"></i></span></td></tr>')
        out.append("</table></div></details>")
    out.append("</section>")
    out.append('<section><h2>By file type, all versions</h2><p class="note">Update feeds and update packages '
               "count the app checking for or fetching updates, not new players.</p>"
               '<div class="tbl"><table><tr><th>Type</th><th class="n">Files</th><th class="n">Downloads</th></tr>')
    for t in summary["types"]:
        out.append(f'<tr><td>{e(t["type"])}</td><td class="n">{t["files"]}</td>'
                   f'<td class="n">{t["downloads"]:,}</td></tr>')
    out.append("</table></div></section>")
    out.append("<section><h2>Total downloads over time</h2>" + render_trend(trend or []) + "</section>")
    out.append("<footer>One snapshot per run, twice a day.</footer></main></body></html>\n")
    return "\n".join(out)


def render_markdown(summary, repo, generated):
    """The same content as the HTML page, as GitHub-flavoured markdown."""
    def cell(s):
        return str(s).replace("|", "\\|")
    out = [f"# Download stats: {repo}", "",
           f"**{summary['total']:,}** total downloads, all files and versions.", "",
           f"Last updated {generated}. Update-feed files count updater checks, not players.", "",
           "## By file type, all versions", "",
           "| Type | Files | Downloads |", "|---|---:|---:|"]
    for t in summary["types"]:
        out.append(f"| {cell(t['type'])} | {t['files']} | {t['downloads']:,} |")
    out += ["", "## By version", ""]
    for v in summary["versions"]:
        flag = " (draft)" if v["draft"] else " (pre-release)" if v["prerelease"] else ""
        out += [f"### {v['tag']}{flag}", "", f"{v['date']} · {v['downloads']:,} downloads", "",
                "| File | What it is | Size | Downloads |", "|---|---|---:|---:|"]
        for f in v["files"]:
            out.append(f"| {cell(f['name'])} | {cell(f['description'])} | "
                       f"{human_size(f['size'])} | {f['downloads']:,} |")
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
        f.write(render(summary, args.repo, now, load_trend(args.data_dir)))
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
