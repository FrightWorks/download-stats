# download-stats

Current numbers: [STATS.md](STATS.md)
Download stats for FrightWorks releases, per file and per version


## Run

```
python dashboard.py            # Python 3, standard library only
python -m unittest discover -s tests
```

It lists every release of `FrightWorks/FrightWorks-no100f` with each asset's download count, using `gh api`
if `gh` is installed and logged in, otherwise the unauthenticated REST API (set `GITHUB_TOKEN` to raise the rate limit).
Options: `--repo OWNER/NAME`, `--out PATH`, `--map PATH`, `--data-dir PATH`.

## Output

All committed, and regenerated on every run:

- `STATS.md`: the stats as markdown tables (GitHub renders it in the repo view): grand total, rollup by file type,
  and per version (newest first, with release date) each file's description, size and downloads, plus a "last updated" UTC time.
- `output/index.html`: the same as a self-contained page (no scripts, no external requests). Committed so it can be
  downloaded and opened locally; private repos on the free plan have no Pages.
- `data/<UTC timestamp>.json`: raw per-asset counts from each run. One file per run (the name includes the time, so
  twice a day never collides). GitHub only reports cumulative counts, so these are the history for trends later.

Note: the `RELEASES`, `releases.*.json`, `assets.*.json` and `.nupkg` files are fetched by the in-app updater, so
their counts reflect update checks and installs, not extra players.

## Schedule

`.github/workflows/stats.yml` runs at 03:00 and 15:00 America/Chicago (the `timezone` key on the `schedule` trigger,
which GitHub documents; it follows daylight saving). It runs the tests and `dashboard.py` with the repo's own
`GITHUB_TOKEN` (FrightWorks-no100f is public, so no extra secret), then commits and pushes `STATS.md`, `output/` and
`data/` as github-actions[bot] if anything changed. Run it by hand from the Actions tab ("Run workflow").
Scheduled and manual runs only work once the workflow is on the default branch.

## Editing the file descriptions

`asset-types.json` maps an asset name pattern (`fnmatch` wildcards, case-insensitive) to a `type` (the rollup label)
and a `description` (shown per file). The first matching rule wins, so put specific patterns above general ones.
Assets that match nothing show as "unclassified"; the script also lists them on stderr.
