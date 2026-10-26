// codelore VS Code extension: hover a line to see why it exists.
// Talks to `codelore serve`, which must be running against this workspace's repository.
const vscode = require("vscode");
const path = require("path");

const cache = new Map(); // "file:line:version" -> Promise<result>

function config() {
  return vscode.workspace.getConfiguration("codelore");
}

function relativePath(document) {
  const folder = vscode.workspace.getWorkspaceFolder(document.uri);
  if (!folder) return null;
  return path.relative(folder.uri.fsPath, document.uri.fsPath).split(path.sep).join("/");
}

async function fetchWhy(document, line) {
  const rel = relativePath(document);
  if (!rel) return null;
  const key = `${rel}:${line}:${document.version}`;
  if (!cache.has(key)) {
    const url = `${config().get("serverUrl")}/api/why?path=${encodeURIComponent(rel)}&line=${line}`;
    cache.set(key, fetch(url).then(async (r) => {
      const body = await r.json();
      if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
      return body;
    }));
  }
  try {
    return await cache.get(key);
  } catch (e) {
    cache.delete(key);
    throw e;
  }
}

function linkify(text, evidence) {
  // Turn [C1] / [P2] citations into links to the commit or PR.
  const urls = Object.fromEntries(evidence.filter((e) => e.url).map((e) => [e.id, e.url]));
  return text.replace(/\[([A-Z]+\d+)\]/g, (m, id) => (urls[id] ? `[\\[${id}\\]](${urls[id]})` : m));
}

function render(result) {
  const md = new vscode.MarkdownString(undefined, true);
  md.isTrusted = false;
  md.appendMarkdown("**$(history) Why is this here?**\n\n");
  if (result.summary) md.appendMarkdown(`**${result.summary}**\n\n${linkify(result.story, result.evidence)}\n\n`);
  if (result.error) md.appendMarkdown(`_${result.error}_\n\n`);
  if (result.evidence.length) {
    md.appendMarkdown("---\n");
    for (const e of result.evidence.slice(0, 6)) {
      const label = e.url ? `[${e.id}](${e.url})` : e.id;
      const title = e.title.replace(/[\[\]]/g, "");
      md.appendMarkdown(`${label} · ${e.date} · ${e.author} — ${title}  \n`);
    }
  }
  return md;
}

function delay(ms, token) {
  return new Promise((resolve) => {
    const t = setTimeout(resolve, ms);
    token.onCancellationRequested(() => { clearTimeout(t); resolve(); });
  });
}

function activate(context) {
  const provider = {
    async provideHover(document, position, token) {
      // The server reads the file on disk, so unsaved edits would shift line numbers.
      if (!config().get("hover") || document.isDirty || document.lineAt(position.line).isEmptyOrWhitespace) return null;
      await delay(config().get("hoverDelayMs"), token);
      if (token.isCancellationRequested) return null;
      try {
        const result = await fetchWhy(document, position.line + 1);
        return result ? new vscode.Hover(render(result)) : null;
      } catch (e) {
        return new vscode.Hover(new vscode.MarkdownString(`codelore: ${e.message}. Is \`codelore serve\` running?`));
      }
    },
  };
  context.subscriptions.push(
    vscode.languages.registerHoverProvider({ scheme: "file" }, provider),
    vscode.commands.registerCommand("codelore.why", async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) return;
      const line = editor.selection.active.line + 1;
      try {
        const result = await vscode.window.withProgress(
          { location: vscode.ProgressLocation.Notification, title: `codelore: reading history of line ${line}…` },
          () => fetchWhy(editor.document, line),
        );
        const panel = vscode.window.createWebviewPanel("codelore", `Why line ${line}?`, vscode.ViewColumn.Beside, {});
        panel.webview.html = `<pre style="white-space:pre-wrap;font-family:var(--vscode-font-family)">${escapeHtml(
          [result.summary, result.story, result.error, "", ...result.evidence.map((e) => `[${e.id}] ${e.date} ${e.author}: ${e.title} ${e.url || ""}`)]
            .filter((x) => x != null).join("\n\n"))}</pre>`;
      } catch (e) {
        vscode.window.showErrorMessage(`codelore: ${e.message}`);
      }
    }),
    vscode.commands.registerCommand("codelore.openDashboard", () =>
      vscode.env.openExternal(vscode.Uri.parse(config().get("serverUrl")))),
  );
}

function escapeHtml(s) {
  return s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}

function deactivate() {}

module.exports = { activate, deactivate };
