# CI

Pull requests were going red on the first push because of three things that are not product breaks: the Pixel UI browser suite (in particular `diScanFace` SINGLE HOME) has no path filter and, until this change, no retry; missions were pushing after only the tests someone thought were affected; and CodeQL's default setup was failing the code-scanning rule on test helpers.

## What the ruleset requires

The active ruleset is **main - required CI checks**. It requires these status checks:

- `macos-latest / Python 3.13`
- `ubuntu-latest / Python 3.13`
- `ubuntu-latest / Python 3.9`
- `hermetic Git / macos-latest / Python 3.13`
- `hermetic Git / ubuntu-latest / Python 3.13`
- `hermetic Git / ubuntu-latest / Python 3.9`
- `Pixel validation`
- `Dependency review`
- `Gitleaks (all refs, full history, redacted)`

It also requires CodeQL code scanning: alerts at the `errors` threshold, and security alerts at high or higher.

There is a separate ruleset, **main - collaborator review required**. Branch protection on the REST branch-protection endpoint is not what enforces this; the rulesets are.

## Runners and check names

Linux jobs run on `ubuntu-24.04`. `ubuntu-latest` is Ubuntu 26.04, which has no Python 3.9 build, so 3.9 stays on 24.04. macOS jobs run on `macos-26`, which is the image a green `macos-latest` job was actually using (`macos-26-arm64`).

The 3.13 check **names** are still `ubuntu-latest / Python 3.13` and `macos-latest / Python 3.13`, because those are the strings the ruleset requires. Renaming them without editing the ruleset leaves the required check pending forever. The Python 3.9 job is already named `ubuntu-24.04 / Python 3.9`. The ruleset still also lists `ubuntu-latest / Python 3.9` and `hermetic Git / ubuntu-latest / Python 3.9`, which the workflow never produced. Two small jobs now report those exact names. They pass when the real Python job passed, including when the suite was skipped, and fail when it failed. They do not run the tests again.

To make the check names match the pinned runners, edit the ruleset and replace:

- `ubuntu-latest / Python 3.13` with `ubuntu-24.04 / Python 3.13`
- `macos-latest / Python 3.13` with `macos-26 / Python 3.13`
- `hermetic Git / ubuntu-latest / Python 3.13` with `hermetic Git / ubuntu-24.04 / Python 3.13`
- `hermetic Git / macos-latest / Python 3.13` with `hermetic Git / macos-26 / Python 3.13`
- `ubuntu-latest / Python 3.9` with `ubuntu-24.04 / Python 3.9`
- `hermetic Git / ubuntu-latest / Python 3.9` with `hermetic Git / ubuntu-24.04 / Python 3.9`

Then the two compatibility jobs can be removed. Until that edit, leave the names as they are.

## What a change runs

`scripts/ci_path_filter.py` reads the diff.

- A docs or README-only change skips the Pixel UI browser shards. Pixel checks (types, lint, server tests, build) still run. The **Pixel validation** job exits success and says the UI suite was skipped, so the required check does not stay pending.
- A change that only touches `integrations/pixel-agents/webview-ui/` skips the Python suite. The Python jobs still run, skip the test steps, and exit success under the required check names.
- Anything else, a manual run, or a diff that could not be read runs both suites.

Each Pixel UI shard retries once after a failure. The log line starts with `RETRY:`. A second failure is the result. The retry exists only in that CI step.

Long-task and frame-time budgets in `livingCityLoad` are printed on pull requests and do not fail them. `.github/workflows/pixel-perf-nightly.yml` runs that file with `PIXEL_PERF_GATE=1`, which does fail.

## Before commit and push

`scripts/pre-pr-ci.sh` is the local mirror of CI: the Python runner CI uses, the static and hermetic suites, the compile and whitespace checks, then Pixel types, lint, server tests, AsyncAPI generate and validate, and the full UI suite. The Python suite includes the bound-constant pin list. The Pixel server tests include the fixture export checks.

Herd delivery and anyone else committing run that script before commit and before push. A CI fix is not pushed until the suite that failed is green locally. The script's header says the same thing.

## CodeQL

`.github/workflows/codeql.yml` is an advanced-setup workflow. `.github/codeql/codeql-config.yml` ignores `**/test/**`, `**/tests/**`, `**/__tests__/**`, fixture paths, and `*.test` / `*.spec` files, so a test helper cannot fail the pull request.

Default setup does not read that config. It has to be turned off or the two setups both run and default setup can still fail the ruleset.

In the GitHub UI, on this repository:

1. Open **Settings → Code security** (on some accounts this is **Settings → Advanced Security**, then **Code scanning**).
2. Find **CodeQL analysis**.
3. Open the **⋯** menu on that row and choose **Switch to advanced** if it is offered. If the menu offers **Disable CodeQL** for the default setup instead, disable the default setup. The workflow file in this repo is the scan that remains.
4. Confirm the next pull request's code-scanning result comes from the **CodeQL** workflow, and that default setup is not still listed as enabled.

That click cannot be done from the repository files. Until it is done, default setup keeps scanning test helpers.
