# Project

A Project is a Claw-owned entity that gives one Claw a persistent working context. Its Main and managed worker Threads share that context. Service Workspace is the administrative and authorization scope for the native resources the Project references.

## Working Context

A Project uses one persistent Service Environment, created from a selected template or attached as an existing Environment. Working files and configured tooling, including [reply CLIs](03-connectors-and-replies.md#project-setup), are available in the same Environment across Runs. An optional persistent Service Browser provides browser state across Runs. Main going idle does not stop the resources or the workers using them.

Each new Claw Project creates a persistent native Service Memory Space backed by Service-managed PostgreSQL File Memory. Claw records the Space reference, and Main and its workers use the same authorized Space across Runs and Environment replacement. Service owns memory and its access grants. Shared files, memory, and browser state do not merge the Threads' conversation histories. Any broader memory sharing comes from explicitly authorized Service resource access, not from installing Claw or placing Claws in the same Workspace.

## Agent Composition

Claw automatically supplies useful native capabilities to Main and workers according to the Environment, optional Browser, and other resources actually available to each execution. These include file and command operations, context summary, background execution, and collaboration where supported and authorized. Service's resource grants and execution policy remain authoritative. Operators do not select individual default tools for each Claw.

Claw uses a selected Service Agent and contributes the system instructions needed for its Inbox, communication, delegation, and [scheduled work](02-main-thread.md#scheduled-work). The execution includes those instructions and the automatic capabilities alongside the Agent's configured instructions, MCP, and other toolsets; Claw's contribution does not discard the Agent's configuration. Configure additional MCP and other toolsets on that Agent in Service. An Agent with no operator-authored tools or instructions can work when a usable model and the required resources are available; unavailable resources do not become available merely through composition.

An operator can provide worker model choices with descriptions of the work each suits. Claw includes this guidance in Main's instructions. Main chooses a suitable model when delegating; descriptions do not impose a fixed router.

## Continuity and Ownership

Claw records the Project's resource references and its Thread associations. Service owns preparation, access, and lifecycle of the native resources, and its grants apply to each use. When a required resource is missing, unavailable, or not authorized, surface the affected Project and operation; an empty replacement cannot stand in for the original working state. Recovery or replacement is an explicit operator action. The ownership boundary also applies to [backup and restore](05-operator-workflows.md#backup-and-restore): restoring a Project reference does not restore the data behind it.
