# download-stats
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

- `output/index.html`: self-contained page (no scripts, no external requests): grand total, a rollup by file type,
  and per version (newest first, with release date) each file's description, size and downloads. Git-ignored: it is
  regenerated from the API on every run.
- `data/<UTC timestamp>.json`: raw per-asset counts from each run, committed. GitHub only reports cumulative counts,
  so these snapshots are the history for trends later. Commit new ones after a run.

Note: the `RELEASES`, `releases.*.json`, `assets.*.json` and `.nupkg` files are fetched by the in-app updater, so
their counts reflect update checks and installs, not extra players.

## Editing the file descriptions

`asset-types.json` maps an asset name pattern (`fnmatch` wildcards, case-insensitive) to a `type` (the rollup label)
and a `description` (shown per file). The first matching rule wins, so put specific patterns above general ones.
Assets that match nothing show as "unclassified"; the script also lists them on stderr.

## Possible later

A scheduled GitHub Action could run the script daily and commit the snapshot.
