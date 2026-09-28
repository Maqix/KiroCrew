# Kanban boards

Project boards for multi-phase work, managed with the
[kanban-md](https://github.com/antopolskiy/kanban-md) CLI. Each board is a
directory holding `config.yml` and one Markdown card per task under `tasks/`.

Drive a board through the CLI, never by editing card files: the CLI owns ids,
file names, dependencies and the activity log.

```bash
kanban-md --dir docs/kanban/<board> board --compact    # summary
kanban-md --dir docs/kanban/<board> list --compact     # every card
kanban-md --dir docs/kanban/<board> show <id>          # one card
kanban-md --dir docs/kanban/<board> move <id> --next   # advance a card
```

A board tracks work; it does not record decisions. What a change is and why it
was agreed live in its RFC under
[../request-for-change/](../request-for-change/README.md).

| Board | Tracks |
|---|---|
| `one-chat-first-run/` | [rfc-one-chat-first-run.md](../request-for-change/rfc-one-chat-first-run.md): one command to a delivered job in a single conversation, plus its north-star follow-ups |
