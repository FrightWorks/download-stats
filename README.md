# download-stats

Download stats for FrightWorks releases, per file and per version.

Live page: https://frightworks.dev/stats/. It loads https://frightworks.dev/stats.json, which the FrightWorks web
server regenerates from GitHub every 5 minutes with this repo's `dashboard.py`.


## Run

```
python dashboard.py            # Python 3, standard library only
python -m unittest discover -s tests
```

It lists every release of `FrightWorks/FrightWorks-no100f` with each asset's download count, using `gh api`
if `gh` is installed and logged in, otherwise the REST API (set `GITHUB_TOKEN` to raise the rate limit; when it's set,
the REST API is used even if `gh` is installed). Options: `--repo OWNER/NAME`, `--out PATH`, `--md PATH`,
`--json PATH`, `--map PATH`, `--data-dir PATH`, plus the two the server uses:

- `--cache PATH`: keeps the last API answer and its ETag, and asks GitHub conditionally (`If-None-Match`). When
  nothing changed GitHub answers 304 Not Modified, and an authorized 304 doesn't count against the rate limit.
- `--prune`: thins out old snapshots: all of the last 2 days, then the last one of each UTC hour up to 30 days, then
  the last one of each US Central day (enough for the 7-day change and the per-day chart).

`--out ""` and `--md ""` skip the redirect page and the markdown table (`--json` is then required).

## Output

- `stats.json`: the numbers for https://frightworks.dev/stats/ (`schema` 1; versions, per-platform numbers, the
  all-versions total, the 7-day change, new downloads per day). The page reads it at build time (the fallback) and in
  the browser when it opens, so keep existing fields stable and bump `DATA_SCHEMA` in `dashboard.py` if one has to
  change meaning or go away.
  "Downloads" = Windows installer + zip, or the Linux AppImage + tarball (GitHub counts requests, not people). "Update files" =
  that version's own `.nupkg` packages. "Update checks" = the busiest update-feed file (`releases.*.json`, `RELEASES*`).
- `<data-dir>/<UTC timestamp>.json`: raw per-asset counts from each run. GitHub only reports cumulative counts, so these
  snapshots are the history the 7-day change and the per-day chart come from.
- `output/index.html` and `STATS.md` (local runs only, git-ignored): a redirect to the stats page, and a markdown table.

Note: the `RELEASES`, `releases.*.json`, `assets.*.json` and `.nupkg` files are fetched by the in-app updater, so
their counts reflect update checks and installs, not extra players.

## Where it runs

On the FrightWorks web VM (set up from the `frightworks-infra` repo): a systemd timer runs `dashboard.py` every
5 minutes with `--cache` and `--prune`, the snapshots in `/var/lib/frightworks-stats/data` and `stats.json` in
`/srv/stats`, which nginx serves as https://frightworks.dev/stats.json. That's one GitHub API request per run, at most
12 an hour, many of them free 304s. A new no100f release shows up on the stats page within about 5 minutes.

The snapshots live only on the VM since 2026-11-27. `data/` here holds the ones GitHub Actions collected before that
(from 2026-11-12), which seeded the VM. The GitHub Actions workflows that collected them were retired at the same time.

Rule changes in `asset-types.json` reach the VM by themselves: it checks this file on `main` at most once an hour
and uses it as long as it's valid and each rule's `type` is one the VM already knows. Changes to `dashboard.py`, or a
rule with a new `type`, reach the VM when they're installed again from `frightworks-infra` (see its `docs/10-stats.md`).

## Editing the file descriptions

`asset-types.json` maps an asset name pattern (`fnmatch` wildcards, case-insensitive) to a `type` (the rollup label)
and a `description` (shown per file). The first matching rule wins, so put specific patterns above general ones.
Assets that match nothing show as "unclassified"; the script also lists them on stderr.
