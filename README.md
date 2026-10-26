# codelore

Code history you can ask questions of, powered by Meta's Muse models.
Built during the Meta Global AI Developer Hackathon (Oct 26 – Nov 4, 2026). Everything in this
folder is new hackathon work; the sibling folders in this repository predate the event.

## Demo features

| Feature | What it does | Model use |
|---|---|---|
| **Why is this here?** | Hover a line in VS Code to see the story behind it from git history and PRs | Muse Spark narrates; every claim cites a commit or PR, unknown citations are stripped |
| **Heat map** | Ranks files by churn, bug-fix pressure and bus factor ("only Rahul knows this") | Scores are pure git; Muse Spark optionally writes a one-line note per risky file |
| **Blameless postmortem** | Incident thread + logs (+ call audio) → postmortem draft | Muse Spark drafts, Muse Voice Transcribe for audio; uncited claims go to "needs review", blame language is rewritten |

## Roadmap (pitch only, not built)

Onboarding in a day · API breakage radar · Secret leak time bomb · On-call co-pilot

## Status

- Git analysis, heat map, server, dashboard, VS Code extension and tests: written; 16 tests pass.
- **Model calls are untested**: the Meta Model API request shape in `codelore/llm.py` follows the
  OpenAI-compatible convention and must be checked against the official docs.

## Usage

```bash
pip install -e .
export META_API_KEY=... META_API_BASE=... META_MODEL=...   # from the Meta Model API docs
codelore --repo path/to/repo heatmap
codelore --repo path/to/repo why src/app.py:120
codelore postmortem --thread thread.txt --logs app.log --out postmortem.md
codelore --repo path/to/repo serve        # dashboard at http://127.0.0.1:8765
```

VS Code: open `vscode/` in VS Code and press F5 with `codelore serve` running.
Set `GITHUB_TOKEN` for higher GitHub API limits when fetching PR context.
