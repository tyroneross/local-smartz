## Bookmark — Context Snapshots

This project uses @tyroneross/bookmark for context snapshots.

**Automatic behavior:**
- Snapshots captured before compaction and on session end
- Context restored automatically on session start
- Adaptive thresholds increase snapshot frequency with compaction count
- Time-based snapshots every 20 minutes (configurable)

**Commands:**
- `/bookmark:snapshot` — Manual snapshot
- `/bookmark:restore` — Restore from a snapshot
- `/bookmark:status` — Show snapshot stats
- `/bookmark:list` — List all snapshots

The system operates with zero context window tax — all processing runs externally.
