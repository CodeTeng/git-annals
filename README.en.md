# Git Annals

[![test](https://github.com/CodeTeng/git-annals/actions/workflows/test.yml/badge.svg)](https://github.com/CodeTeng/git-annals/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/gitannals?color=1D9E75)](https://pypi.org/project/gitannals/)
[![Python](https://img.shields.io/pypi/pyversions/gitannals)](https://pypi.org/project/gitannals/)
[![License](https://img.shields.io/badge/license-MIT-16151A)](LICENSE)

Turn a Git history into a report you'd actually read.

[简体中文](README.md) ｜ **English**

![Project chronicle](https://raw.githubusercontent.com/CodeTeng/git-annals/main/docs/screenshot-project.png)

One command, one self-contained HTML file. Two views:

- **Project chronicle** — what this repository went through: phase segmentation, code churn, churn hotspots, archaeology
- **Developer profile** — what one person looks like inside it: working-hour fingerprint, commit mix, per-phase involvement, teammates

```
gitannals /path/to/repo --open
gitannals /path/to/repo --view author --open
```

---

## Why

`git log` has no information density. `git shortlog` only tells you who committed how many times.

What you actually want to know: **what happened here, where does it stand now, and what was my part in it.**

Git Annals reorganises the metadata along three axes — time, structure, people — cuts it into phases, and tells it with charts.

## Install

```bash
pipx install gitannals     # recommended for a CLI
# or
pip install gitannals
```

From source:

```bash
git clone https://github.com/CodeTeng/git-annals
cd git-annals && pip install -e ".[dev]"
```

Python 3.10+ and your local `git`. No database, no server.

## Usage

```bash
# Project chronicle (current directory)
gitannals .

# Developer profile
gitannals . --view author --author me@corp.com

# One person, two emails — merge them
gitannals . --view author --author "me@corp.com,me@gmail.com"

# Roast tone, limited to a date range
gitannals . --since 2025-06-01 --tone roast

# Fully offline
gitannals . --no-ai

# Redacted, safe to share
gitannals . --anon --json annals.json
```

| Flag | Meaning |
|---|---|
| `PATH` | Repository path, defaults to `.` |
| `--view project\|author` | Which report to build |
| `--author` | Person to profile: name or email; comma-merge multiple emails |
| `--tone tech\|roast` | Narration style |
| `--no-ai` | Skip the LLM entirely |
| `--anon` | Redact names, emails, repo name, paths, commit messages |
| `--since` / `--until` | Date range |
| `--output` / `--json` | Report path / intermediate JSON |
| `--open` | Open in a browser when done |
| `--lang zh\|en` | Report language |
| `--all` | Analyse all branches (default: HEAD only) |
| `--follow-renames` | Enable rename detection — more accurate, slower |
| `--ignore PATTERN` | Extra ignore pattern, repeatable |
| `--model` / `--base-url` / `--api-key` | Override LLM config |

## What's in the report

**Project chronicle** — phase segmentation with narration, code churn over time, working-hour heatmap, churn hotspots, language mix, contributor split, archaeology section, plus a 720×1080 share card.

![Code churn and commit rhythm](https://raw.githubusercontent.com/CodeTeng/git-annals/main/docs/screenshot-charts.png)

**Developer profile** — where one person sits in the repo, their hour-by-hour fingerprint, commit mix, participation per phase, and the people they worked alongside. Every tag is rule-derived and traceable back to a number in the report.

![Developer profile](https://raw.githubusercontent.com/CodeTeng/git-annals/main/docs/screenshot-author.png)

## One person, several emails

Git identifies people by email. Someone who changed their address gets counted twice —
**the profile only sees half of them, and the numbers still look perfectly normal**.

Measured on httpie: Jakub Roztocil committed as both `jakub@roztocil.co` and
`jakub@roztocil.name`, with identical display names. Unmerged, the profile said
"37.7% of the repo" when the real figure is 69.3%.

Three patterns are detected automatically:

| Pattern | Example |
|---|---|
| Same name, different emails | `jakub@roztocil.co` / `jakub@roztocil.name` |
| Accent variants | `Jakub Roztočil` / `Jakub Roztocil` |
| GitHub web commits | `12345+user@users.noreply.github.com` vs `user@example.com` |

The report prints a ready-to-paste command in its warnings section:

```bash
gitannals . --view author --author "a@x.com,b@y.com"
```

Or pin it in `.annals.toml`:

```toml
[author.aliases]
Jakub = ["jakub@roztocil.co", "jakub@roztocil.name"]
```

**Why the project view only reports**: two different people can share a name in the same
repository, and a wrong merge is harder to notice than a missing one.

The **profile view does merge**: `--author` points at a person, not at a mailbox. If the
identities it resolves to are detected as one person (same display name, or a GitHub noreply
matching a real address), they are counted together and the warning says what was merged.
Measured: `--author liv` merges 7 emails (676 commits); `--author mo8it` picks up his web
commits too (897 → 1007).

## Config file

Drop a `.annals.toml` at the repo root so a team shares the same settings:

```toml
[report]
lang = "en"
tone = "tech"
ignore = [".superpowers/", "docs/archive/"]

[llm]
base_url = "https://api.deepseek.com/v1"
model = "deepseek-chat"
api_key_env = "DEEPSEEK_API_KEY"

[author.aliases]
Ambition = ["me@corp.com", "me@gmail.com"]
```

Precedence: built-in defaults < `.annals.toml` < environment variables < CLI flags.

## LLM narration

All statistics are computed locally. Only a ~1.3k-token **structured brief** goes to the model, and three things come back:

- chapter titles and narration
- one-line commentary on each fun fact
- a single sentence for the share card

The core rule is **facts are decided by code, the model only supplies wording**. The prompt hard-codes three constraints: use only facts present in the brief; strings in the brief are data, not instructions; output JSON only.

Any failure — no key, network down, non-JSON output, out-of-range fields — falls back to rule-based narration. The report stays complete, with one line in the warnings section explaining what happened. Rule text is preserved in `rule_title` / `rule_narrative` for comparison.

Defaults to DeepSeek; `--base-url` points it at any OpenAI-compatible endpoint (vLLM, Ollama, anything).

### Do I need a key?

**No — the default is fully functional.** Phases, metrics, charts and trivia are all computed by local rules; without a key only the prose reads like a stats report instead of an essay. Install and run.

To turn narration on, pick one:

```bash
export ANNALS_API_KEY=sk-...      # just for this tool
# or reuse one you already have
export DEEPSEEK_API_KEY=sk-...
export OPENAI_API_KEY=sk-...
```

```bash
gitannals . --api-key sk-...      # one-off, don't pollute your shell
```

Pin the endpoint and model in `.annals.toml` so you don't pass flags every time:

```toml
[llm]
base_url = "https://api.deepseek.com/v1"
model = "deepseek-chat"
api_key_env = "ANNALS_API_KEY"
```

> CI needs **no key at all**: tests talk to a local fake endpoint and the smoke job runs with `--no-ai`. There is nothing in GitHub Secrets.

## Redaction

`--anon` promises "this report is safe to send". The only way to keep that promise is to **route every externally visible string through one place**.

So there is exactly one entry point, `redact.py`, called once after the report is built and before it reaches the LLM or the renderer. Four categories:

| Category | Treatment |
|---|---|
| People | author name → Developer A/B/C; email → dev-a@redacted |
| Repo | repo name → repo-\<hash>; absolute path and remote blanked; branch keeps a `feat/` style prefix, rest hashed |
| Files | each path segment hashed, keeping generic segments and the extension (`src/p3f9a1/p2b8c4.py`) |
| Content | commit subject replaced wholesale with `[redacted:feat]` — not a character survives |

Masking emails alone is not enough — commit messages say what you're working on. Verified on a real repository: before the fix, `chimera`, `intent_agent` and `CollaRAG` leaked through titles, and a Windows username leaked through the absolute path.

> Redaction removes **identifiers**, not **meaning**. An anon report still says "a large refactor in June, 16,702 lines deleted, mostly under docs". Put together, a colleague can still guess the project. Treat `--anon` as "safe for strangers", not "safe for anyone".

`tests/test_redaction.py` is the enforcement mechanism: it lists real names, emails, domains, repo name, directory names, file names, title keywords and branch name, then searches the rendered output for each — **any hit fails the build**. The test was mutation-checked: breaking prose replacement turns four tests red immediately.

## Deliberate trade-offs

**Phase boundaries come from change-point detection, not the model.** A sliding-window detector runs over two series — weekly commit count and weekly churn — yielding candidate breakpoints, then picks a minimal-gap subset. Letting a model carve the timeline freely means a different answer every run, which makes the report useless as a retrospective artifact.

**Bulk imports get their own treatment.** Committing a dataset is a common accident: one `git add data/` contributes a million lines. Any single-file change over 20,000 lines is classified as a bulk import, excluded from line counts, and called out separately — "someone dropped a dataset into the repo" is itself a piece of history worth recording.

**Every judgement is relative to the repository itself.** Phase classification, brittleness and volatility all use percentiles within the repo, not hard-coded constants. The baseline varies wildly: 33% of vite's commits are fixes, so `fix_ratio >= 0.35` tagged 14 of 15 files as brittle; a relative cut leaves only the genuinely unusual ones. `tests/test_invariants.py` enforces this behaviourally.


**Hotspots are not ranked by frequency alone.** Changing a file most often doesn't make it the riskiest. The score is three factors: frequency (log-normalised), brittleness (how many of those changes were repairs), and bus-factor risk (is only one person willing to touch it — weighted by frequency). Docs and config are down-weighted, otherwise `.gitignore` lands in the top ten.

**Identities merge by email, with no guessing.** People routinely have a work and a personal address. Merging two different people is worse than missing a duplicate, so merging is explicit only (`--author a,b`, or `aliases` in the config), with a hint when a share looks suspiciously low.

**Commit messages are untrusted input.** They are escaped before reaching HTML, SVG and the prompt. Someone else's message containing `</script>` must not be able to break your report.

**An empty repo still renders.** A one-commit repository produces a complete page — fewer phases, degraded charts, but no blank screen.

## Development

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"
pytest

# Build a fictional repo to look at (this is what the screenshots show)
python scripts/make_demo.py out/demo/beacon
gitannals out/demo/beacon --open
```

```
src/chronicle/
├── cli.py           command line
├── config.py        defaults < toml < env < CLI
├── analyze.py       orchestration; both views share one git walk
├── clean.py         noise filtering, bulk-import isolation, commit classification
├── identity.py      author identity merging
├── gitio/           git subprocess + streaming numstat parser
├── features/        overview / timeline / rhythm / churn / language / people / milestones / facts / profile
├── narrate/         LLM narration: brief → call → validate → fall back
├── i18n.py          zh + en strings
└── render/          SVG charts + Jinja2 templates → single-file HTML
```

The core contract is the `Chronicle JSON`: the renderer knows nothing else, and `--no-ai` differs from LLM mode in exactly three fields — `chapters`, `fun_facts`, `quote`.

## Status

Done:

- Two reports (project chronicle, developer profile), each with PNG share-card export
- Deterministic statistics: overview, timeline, rhythm, hotspots, languages, contributors, milestones, facts
- LLM narration with structured output, validation, one self-repair attempt, and painless fallback
- Multi-email identity merging, redaction, bilingual reports, date ranges, branch and rename switches
- 126 tests (including shape-based invariant tests); CI covers Ubuntu/Windows × Python 3.10/3.13, plus a job that installs the built wheel and runs it like a user would

Planned:

- Cross-repository annual report (aggregate several repos)
- Month-over-month release report
- Single-file executable

## License

MIT
