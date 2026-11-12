# download-stats

Live page: https://frightworks.github.io/download-stats/ \nIn-repo view: [STATS.md](STATS.md)
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

Regenerated on every run:

- `STATS.md` (committed): the stats as markdown tables, which GitHub renders in the repo view: grand total, rollup
  by file type, and per version (newest first, with release date) each file's description, size and downloads,
  plus a "last updated" UTC time.
- `output/index.html` (git-ignored, published to GitHub Pages by the workflow): a friendly self-contained page
  (header with last-updated time in Central time, stat tiles, downloads-per-version bars split into players vs update traffic,
  collapsible per-version file details, file-type rollup, and a total-downloads trend from `data/`; no scripts, no external requests). Pages hosts it, so it isn't committed.
- `data/<UTC timestamp>.json` (committed): raw per-asset counts from each run. One file per run (the name includes
  the time, so twice a day never collides). GitHub only reports cumulative counts, so these are the history for trends later.

Note: the `RELEASES`, `releases.*.json`, `assets.*.json` and `.nupkg` files are fetched by the in-app updater, so
their counts reflect update checks and installs, not extra players.

## Schedule

`.github/workflows/stats.yml` runs at 03:00 and 15:00 America/Chicago (the `timezone` key on the `schedule` trigger,
which GitHub documents; it follows daylight saving). It runs the tests and `dashboard.py` with the repo's own
`GITHUB_TOKEN` (FrightWorks-no100f is public, so no extra secret), commits and pushes `STATS.md` and `data/` as
github-actions[bot] (those commits also keep the schedule from going idle on a public repo), then deploys `output/` to
GitHub Pages (repo setting: Pages source = GitHub Actions). Run it by hand from the Actions tab ("Run workflow").

## Editing the file descriptions

`asset-types.json` maps an asset name pattern (`fnmatch` wildcards, case-insensitive) to a `type` (the rollup label)
and a `description` (shown per file). The first matching rule wins, so put specific patterns above general ones.
Assets that match nothing show as "unclassified"; the script also lists them on stderr.
