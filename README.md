# codelore

**Code history you can ask questions of, powered by Meta's Muse models.**

Every team has code nobody understands and people nobody can afford to lose. codelore turns
git history, pull requests and incident threads into answers: why a line exists, where the
risk lives, and what really happened during an outage.

> **Meta Global AI Developer Hackathon 2026** · Track: **Build for Your World** · Category: Indie Developer
> All code in this repository was written during the hackathon (Oct 26 – Nov 4, 2026);
> see the commit history for the timeline. Nothing here comes from an earlier project.

**Demo video:** _link coming soon_

## What it does

| Feature | What it does | Meta model |
|---|---|---|
| **"Why is this here?"** | Hover a line in VS Code to see the story behind it, built from the commits and PR discussions that shaped it | **Muse Spark** narrates; every sentence cites a commit or PR, and citations to anything else are removed |
| **Codebase heat map** | Ranks source files by churn, bug-fix pressure and bus factor, and flags knowledge risk ("only Rahul knows this", "owner inactive 354 days") | Scores are computed from git; **Muse Spark** writes a short note for the riskiest files |
| **Blameless postmortem writer** | Turns an incident chat thread, logs and an optional call recording into a postmortem draft | **Muse Spark** drafts it, **Muse Voice Transcribe** handles the call audio; claims without evidence go to a "needs review" list and blaming language is rewritten |

### Trust by design

The model never gets the last word on facts. History is gathered deterministically from git and
GitHub, each piece of evidence gets an id (`C1`, `P2`, `T5`, `L3`...), and the model must cite those
ids. Anything it can't back up is stripped or set aside for a human to review.

## Roadmap (in the pitch, not built yet)

- **Onboarding in a day**: a guided tour of the repo for new developers
- **API breakage radar**: warns who will break before you change an API
- **Secret leak time bomb**: finds old leaked keys in git history and logs
- **On-call co-pilot**: pulls logs and likely causes when an alert fires

## Quick start

```bash
pip install -e .
export META_API_KEY=...   # uses https://api.meta.ai/v1 and muse-spark-1.3 by default
codelore --repo path/to/repo heatmap
codelore --repo path/to/repo why src/app.py:120
codelore postmortem --thread examples/checkout-outage/thread.txt \
  --logs examples/checkout-outage/app.log examples/checkout-outage/db.log --out postmortem.md
codelore --repo path/to/repo serve        # dashboard at http://127.0.0.1:8765
```

Full setup, the VS Code extension steps and the demo script: [DEMO.md](DEMO.md).
Set `GITHUB_TOKEN` for higher GitHub API limits when fetching PR context.

## Project layout

```
codelore/      Python package: git analysis, Meta API client, heat map, postmortem, local server
  static/      Web dashboard (heat map + postmortem); d3, marked, DOMPurify vendored for offline use
vscode/        VS Code extension: "Why is this here?" hover
examples/      Fictional incident data for the postmortem demo
tests/         pytest suite (runs offline with a fake model)
```

## Status

- Git analysis, heat map, server, dashboard, VS Code extension: working; `pytest` passes (18 tests).
- **Model calls not yet verified against the live API**: waiting on hackathon API credits. The
  request format in `codelore/llm.py` follows the OpenAI-compatible convention and will be checked
  against the Meta Model API docs.

## License

MIT, see [LICENSE](LICENSE). Vendored libraries keep their own licenses, listed in
`codelore/static/vendor/LICENSES.md`.
