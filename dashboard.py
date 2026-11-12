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


def render(summary, repo, generated):
    e = html.escape
    out = [f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Download stats: {e(repo)}</title><style>
body{{font:15px/1.4 system-ui,sans-serif;max-width:60rem;margin:2rem auto;padding:0 1rem;color:#1c1c1e}}
table{{border-collapse:collapse;width:100%;margin:.5rem 0 1.5rem}}
th,td{{text-align:left;padding:.3rem .6rem;border-bottom:1px solid #ddd;vertical-align:top}}
td.n,th.n{{text-align:right;font-variant-numeric:tabular-nums}}
.big{{font-size:2.5rem;font-weight:700}} .muted{{color:#666;font-size:.85rem}}
h3 small{{font-weight:400;color:#666}} @media(prefers-color-scheme:dark){{
body{{background:#1c1c1e;color:#eee}}th,td{{border-color:#444}}.muted,h3 small{{color:#aaa}}}}
</style></head><body>
<h1>Download stats: {e(repo)}</h1>
<div class="big">{summary['total']:,}</div><div class="muted">total downloads, all files and versions.
Update-feed files count updater checks, not players. Generated {e(generated)}.</div>
<h2>By file type, all versions</h2>
<table><tr><th>Type</th><th class="n">Files</th><th class="n">Downloads</th></tr>"""]
    for t in summary["types"]:
        out.append(f"<tr><td>{e(t['type'])}</td><td class=n>{t['files']}</td><td class=n>{t['downloads']:,}</td></tr>")
    out.append("</table><h2>By version</h2>")
    for v in summary["versions"]:
        flag = " (draft)" if v["draft"] else " (pre-release)" if v["prerelease"] else ""
        out.append(f"<h3>{e(v['tag'])}{flag} <small>{e(v['date'])} &middot; {v['downloads']:,} downloads</small></h3>"
                   "<table><tr><th>File</th><th>What it is</th><th class=n>Size</th><th class=n>Downloads</th></tr>")
        for f in v["files"]:
            out.append(f"<tr><td>{e(f['name'])}</td><td>{e(f['description'])}</td>"
                       f"<td class=n>{human_size(f['size'])}</td><td class=n>{f['downloads']:,}</td></tr>")
        out.append("</table>")
    out.append("</body></html>\n")
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
        f.write(render(summary, args.repo, now.strftime("%Y-%m-%d %H:%M UTC")))
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
