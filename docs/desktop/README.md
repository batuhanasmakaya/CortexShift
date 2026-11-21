# CortexShift Desktop

Desktop is a native, local workspace browser for CortexShift. Open an initialized project with the folder picker to see its active Task, recorded progress, workspace lock observation, latest Session, and most recent checkpoint. The project library retains the paths you explicitly register. Removing an entry only removes it from the library.

The Task explorer displays the original Markdown as selectable source and loads long objectives in pages. Task details link to filtered Sessions and historical records. Session details show canonical lifecycle state, known continuity, and recorded context-delivery evidence. Checkpoints and handoffs preserve capture-time test provenance. Settings offers system, light, and dark themes and shows the status of the local Python bridge.

Provider startup, switching, resume, Task editing, and recovery currently use the [CLI](../cli-reference.md). Desktop observations refresh when you select a project or use the refresh button.

- [Developer setup and troubleshooting](development.md)
- [Architecture, IPC and lifecycle](ipc.md)
- [Packaging and platform strategy](packaging.md)
- [External provider integration research](integrations.md)
