# Demo guide

Two parts: how to run everything on your own computer, and the 3-minute video script.

## Part 1 — Run it on your computer

You need: **Python 3.10+**, **Git**, **VS Code**. Commands below are for a terminal
(macOS/Linux Terminal, or PowerShell on Windows: replace `export X=y` with `$env:X="y"`).

```bash
# 1. Get the code
git clone https://github.com/sivanianto123-rgb/workspace.git
cd workspace
git checkout claude/magical-hawking-5grxoh
pip install -e ./codelore

# 2. Get the demo repository (Black, the Python formatter)
git clone https://github.com/psf/black.git ~/black

# 3. Your Meta Model API details (from the Meta developer dashboard; never commit these)
export META_API_KEY="..."
export META_API_BASE="..."          # API base URL from the docs
export META_MODEL="..."             # Muse Spark model id
export META_TRANSCRIBE_MODEL="..."  # Muse Voice Transcribe model id (optional)
export GITHUB_TOKEN="..."           # optional: a GitHub token raises the PR lookup limit

# 4. Start codelore pointed at Black
codelore --repo ~/black serve --since "2 years ago"
```

Leave that terminal running. Open http://127.0.0.1:8765 for the dashboard.

### Try the VS Code hover

1. In VS Code: **File → Open Folder…** and pick `workspace/codelore/vscode`.
2. Press **F5**. A second VS Code window opens, titled "Extension Development Host".
3. In that second window: **File → Open Folder…** and pick `~/black`.
4. Open `src/black/linegen.py`, rest the mouse on any line for about a second.
   A "Why is this here?" box appears. You can also run the command
   **codelore: Why is this line here?** from the Command Palette (Ctrl/Cmd+Shift+P).

If the hover says the server is unreachable, check that step 4 above is still running.

## Part 2 — 3-minute video script

Record screen + voice. Keep the dashboard and VS Code already open and warmed up
(hover each demo line once before recording so answers come back instantly).

| Time | Show | Say |
|---|---|---|
| 0:00–0:20 | Title slide or the dashboard | "Every team has code nobody understands and people nobody can lose. codelore turns git history, PRs and incident threads into answers, using Meta's Muse Spark and Muse Voice Transcribe." |
| 0:20–1:15 | VS Code on Black, hover a line in `linegen.py`, then click a [P1] link | "Why is this line here? Muse Spark reads the commits and PR discussions behind this exact line and tells the story. Every sentence cites its source; anything it can't cite gets removed. No more archaeology." |
| 1:15–2:00 | Dashboard heat map: click `linegen.py`, then `pgen2/tokenize.py` | "The heat map shows where risk lives. linegen.py had 32 bug fixes in two years. And the tokenizer: one person wrote 89% of it, and they've been inactive for almost a year. That's a bus factor of one." |
| 2:00–2:45 | Postmortem tab with `examples/checkout-outage`, click Write, scroll result and checks panel | "When things break, codelore writes the postmortem from the incident thread and logs. Every timeline entry cites a message or log line. And it's blameless: the thread said 'Rahul should have caught this'; the postmortem talks about the missing safeguard instead." |
| 2:45–3:00 | README roadmap section | "Built during this hackathon. Next: onboarding tours, API breakage radar, leaked-secret detection, and an on-call co-pilot." |

Before recording, re-check the numbers you say (fix counts, the 89%, "inactive" days):
they come from Black's live history and will drift.
