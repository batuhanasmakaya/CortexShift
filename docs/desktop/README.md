# CortexShift Desktop

Desktop manages the same Tasks, Sessions, checkpoints, and handoffs as the CortexShift CLI. Start Claude Code or Codex in a native terminal and return to Desktop to switch providers or resume work.

## Create and start a Task

1. Initialize a workspace with `cortexshift init`. In Desktop, choose **Open Project** and select the initialized root.
2. Choose **New Task**, enter a title and objective, or choose **Choose Markdown file**. The preview shows Markdown source. The full selected file is saved with its Unicode and whitespace.
3. Choose **Create Task** to save the Task, or select a provider and choose **Create & start with Claude Code** or **Create & start with Codex**. Starting requires an active Task. When another Task is active, the dialog defaults to creating an inactive Task. You can explicitly make the new Task active and keep the old Task in history.
4. Work in the native terminal that opens. A status message identifies when the worker is preparing context and when the provider process starts. If the launch fails after saving, open the existing Task and use **Start agent** to retry. You can make an inactive Task active from its detail view.

## Switch or resume

- End the current interactive invocation using the provider's own controls. Return to **Overview** and select **Switch to Codex** or **Switch to Claude Code**. Review the destination plan and choose **Open terminal**. The switch uses the active Task and its recorded Session, progress, and handoff evidence.
- Choose **Resume** in Overview for automatic selection. The plan indicates whether it will request exact native resume or continue the Task in a fresh conversation. A provider can still reject a recorded native ID.
- Open a Session from **Sessions** for explicit **Resume this Session**, **Continue Task fresh**, or **Switch to…** actions. Activate its Task first if necessary. The Session links to its checkpoint and handoff when those records exist.
- A held workspace lock blocks transitions. After the provider or retained background work finishes, the activity indicator refreshes. If a Session has no recorded end, open **Review recovery** to preview eligibility and explicitly reconcile eligible Sessions across the project. Ownership can remain uncertain.

## Read the evidence

The action banner reports terminal dispatch, handoff preparation, actual provider process startup, and last observed outcome. **View Session** opens the recorded invocation. Objective delivery shows how much of the canonical objective MCP served. Handoff delivery and objective pages record transport evidence. Test reports and checkpoints describe historical observations with the displayed provenance.

The project library retains registered roots. Removing a library entry leaves the workspace and its history intact. Choose system, light, or dark appearance in **Settings & diagnostics**.

- [Install and troubleshoot Desktop](install.md)
- [Developer setup and troubleshooting](development.md)
- [Architecture, IPC and lifecycle](ipc.md)
- [Packaging and platform strategy](packaging.md)
- [Managed provider and vendor app support](integrations.md)
- [Release engineering](releasing.md)
- [Clean-machine and provider qualification](qualification.md)
- [Dependency security review](security.md)
