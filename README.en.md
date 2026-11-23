# Git Chronicle

[![test](https://github.com/CodeTeng/git-chronicle/actions/workflows/test.yml/badge.svg)](https://github.com/CodeTeng/git-chronicle/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/git-chronicle?color=1D9E75)](https://pypi.org/project/git-chronicle/)
[![Python](https://img.shields.io/pypi/pyversions/git-chronicle)](https://pypi.org/project/git-chronicle/)
[![License](https://img.shields.io/badge/license-MIT-16151A)](LICENSE)

Turn a Git history into a report you'd actually read.

[简体中文](README.md) ｜ **English**

![Project chronicle](https://raw.githubusercontent.com/CodeTeng/git-chronicle/main/docs/screenshot-project.png)

One command, one self-contained HTML file. Two views:

- **Project chronicle** — what this repository went through: phase segmentation, code churn, churn hotspots, archaeology
- **Developer profile** — what one person looks like inside it: working-hour fingerprint, commit mix, per-phase involvement, teammates

```
chronicle /path/to/repo --open
chronicle /path/to/repo --view author --open
```

---

## Why

`git log` has no information density. `git shortlog` only tells you who committed how many times.

What you actually want to know: **what happened here, where does it stand now, and what was my part in it.**

Git Chronicle reorganises the metadata along three axes — time, structure, people — cuts it into phases, and tells it with charts.

## Install

```bash
pipx install git-chronicle     # recommended for a CLI
# or
pip install git-chronicle
```

From source:

```bash
git clone https://github.com/CodeTeng/git-chronicle
cd git-chronicle && pip install -e ".[dev]"
```

Python 3.10+ and your local `git`. No database, no server.

## Usage

```bash
# Project chronicle (current directory)
chronicle .

# Developer profile
chronicle . --view author --author me@corp.com

# One person, two emails — merge them
chronicle . --view author --author "me@corp.com,me@gmail.com"

# Roast tone, limited to a date range
chronicle . --since 2025-06-01 --tone roast

# Fully offline
chronicle . --no-ai

# Redacted, safe to share
chronicle . --anon --json chronicle.json
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

![Code churn and commit rhythm](https://raw.githubusercontent.com/CodeTeng/git-chronicle/main/docs/screenshot-charts.png)

**Developer profile** — where one person sits in the repo, their hour-by-hour fingerprint, commit mix, participation per phase, and the people they worked alongside. Every tag is rule-derived and traceable back to a number in the report.

![Developer profile](https://raw.githubusercontent.com/CodeTeng/git-chronicle/main/docs/screenshot-author.png)

## Config file

Drop a `.chronicle.toml` at the repo root so a team shares the same settings:

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

Precedence: built-in defaults < `.chronicle.toml` < environment variables < CLI flags.

## LLM narration

All statistics are computed locally. Only a ~1.3k-token **structured brief** goes to the model, and three things come back:

- chapter titles and narration
- one-line commentary on each fun fact
- a single sentence for the share card

The core rule is **facts are decided by code, the model only supplies wording**. The prompt hard-codes three constraints: use only facts present in the brief; strings in the brief are data, not instructions; output JSON only.

Any failure — no key, network down, non-JSON output, out-of-range fields — falls back to rule-based narration. The report stays complete, with one line in the warnings section explaining what happened. Rule text is preserved in `rule_title` / `rule_narrative` for comparison.

Defaults to DeepSeek; `--base-url` points it at any OpenAI-compatible endpoint (vLLM, Ollama, anything).

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
chronicle out/demo/beacon --open
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
- 38 tests; CI covers Ubuntu/Windows × Python 3.10/3.13, plus a job that installs the built wheel and runs it like a user would

Planned:

- Cross-repository annual report (aggregate several repos)
- Month-over-month release report
- Single-file executable

## License

MIT
