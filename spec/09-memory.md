# Memory

Service owns memory access, projection, edits, and organization. Claw chooses native scopes for the Instance and its working contexts; it does not run a second organizer or store a copy of native memories.

One Thread mode uses Main's configured shared knowledge across its participating Channels and workers, subject to their grants. Per-Channel and per-trigger modes retain a stable Channel memory context, so a new conversation can start without losing its Channel knowledge or inheriting unrelated conversation history. Related GitHub Channels can use their configured repository context. Common knowledge can be configured across contexts. Sharing across Instances is explicit, not a consequence of selecting the same agent or running in the same deployment.

A scheduled conversation can use the configured working memory without seeing another Thread's private history or files. Mode switches and retention of business records do not move or delete native memories. A removed or inaccessible scope is reported as unavailable. Backups account for the selected durable memory and its metadata.
