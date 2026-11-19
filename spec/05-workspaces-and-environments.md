# Workspaces and Environments

## Working Context

An Instance selects resources within its Service workspace. A Channel or task can select its configured working context without gaining authority to use unrelated files, agents, or credentials. Service owns environment selection, working files, file transfer, browsers, and lifecycle. Claw authorizes exposing an attachment or artifact to the exact external destination separately from the ability to read it in Service.

Conversation, working storage, and browser profile have independent lifetimes. The persistent Docker workflow retains the selected working directory, home or cache, and durable outputs across compatible work and ordinary restarts. A named persistent environment that loses its data is unavailable until recovered; a fresh empty environment is not the same working context. Docker access belongs to the explicitly selected workload, not every Instance.

The shared visible browser workflow preserves its configured profile and human takeover and handback. Browser sessions and files may be shared where configured and authorized. Rebinding or removing a business association does not delete a native environment or its files, and current working files are not cloned merely by starting a new conversation.
