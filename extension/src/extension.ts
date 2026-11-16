import * as vscode from "vscode";
import { execFile } from "child_process";
import { promisify } from "util";

const execFileAsync = promisify(execFile);

class AtlasClient {
  constructor(private readonly context: vscode.ExtensionContext) {}

  private endpoint(): string {
    return vscode.workspace.getConfiguration("atlas").get<string>("endpoint", "http://127.0.0.1:8765");
  }

  private token(): string {
    return vscode.workspace.getConfiguration("atlas").get<string>("token", "");
  }

  async rpc<T = unknown>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    const token = this.token();
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
    let response: Response;
    try {
      response = await fetch(`${this.endpoint()}/rpc`, {
        method: "POST",
        headers,
        body: JSON.stringify({ jsonrpc: "2.0", id: Date.now(), method, params }),
      });
    } catch (error) {
      throw new Error(
        `Atlas daemon is not reachable at ${this.endpoint()}. ` +
          `Run "Atlas: Start Local Server" or \`atlas serve\`. (${String(error)})`
      );
    }
    const data = (await response.json()) as { result?: T; error?: string };
    if (data.error) {
      throw new Error(data.error);
    }
    return data.result as T;
  }

  async ensureSession(): Promise<string> {
    const existing = this.context.workspaceState.get<string>("atlas.sessionId");
    if (existing) {
      return existing;
    }
    const session = await this.rpc<{ id: string }>("session.create", {
      title: "VSCode session",
    });
    await this.context.workspaceState.update("atlas.sessionId", session.id);
    return session.id;
  }
}

function workspacePath(): string {
  const folder = vscode.workspace.workspaceFolders?.[0];
  return folder ? folder.uri.fsPath : process.cwd();
}

export function activate(context: vscode.ExtensionContext): void {
  const output = vscode.window.createOutputChannel("Atlas");
  const client = new AtlasClient(context);
  context.subscriptions.push(output);

  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
  status.text = "$(hubot) Atlas";
  status.tooltip = "Atlas Coding Agent";
  status.command = "atlas.chat";
  status.show();
  context.subscriptions.push(status);

  context.subscriptions.push(
    vscode.commands.registerCommand("atlas.chat", async () => {
      const message = await vscode.window.showInputBox({
        prompt: "Ask Atlas to do something in this workspace",
        placeHolder: "e.g. fix the failing test in calc.py",
      });
      if (!message) {
        return;
      }
      output.show(true);
      output.appendLine(`\n> ${message}`);
      try {
        const sessionId = await client.ensureSession();
        const result = await client.rpc<{ content: string }>("chat.send", {
          session_id: sessionId,
          message,
        });
        output.appendLine(result.content || "(empty response)");
      } catch (error) {
        output.appendLine(`\n[error] ${String(error)}`);
        vscode.window.showErrorMessage(String(error));
      }
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("atlas.doctor", async () => {
      try {
        const result = await client.rpc("doctor");
        output.show(true);
        output.appendLine(JSON.stringify(result, null, 2));
      } catch (error) {
        vscode.window.showErrorMessage(String(error));
      }
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("atlas.init", async () => {
      const executable = vscode.workspace.getConfiguration("atlas").get<string>("executable", "atlas");
      try {
        const { stdout, stderr } = await execFileAsync(executable, ["init", "--path", workspacePath()]);
        output.show(true);
        output.appendLine(stdout || stderr || "Atlas initialized.");
      } catch (error) {
        vscode.window.showErrorMessage(
          `Failed to run "${executable} init": ${String(error)}. Install with \`pip install -e .\`.`
        );
      }
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("atlas.startServer", () => {
      const executable = vscode.workspace.getConfiguration("atlas").get<string>("executable", "atlas");
      const terminal = vscode.window.createTerminal({
        name: "Atlas server",
        cwd: workspacePath(),
      });
      terminal.show();
      terminal.sendText(`${executable} serve --port 8765`, true);
    })
  );
}

export function deactivate(): void {
  // Nothing to dispose beyond the context subscriptions.
}
