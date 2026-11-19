# Project

A Project is a Claw-owned entity that gives one Claw a persistent working context. Its Main and managed worker Threads share that context. Service Workspace is the administrative and authorization scope for the native resources the Project references.

## Working Context

A Project uses one persistent Service Environment, created from a selected template or attached as an existing Environment. Working files and configured tooling, including [reply CLIs](03-connectors-and-replies.md#project-setup), are available in the same Environment across Runs. An optional persistent Service Browser provides browser state across Runs. Main going idle does not stop the resources or the workers using them.

Where memory is configured, Main and its workers use the same authorized Project memory. Service owns memory and its access grants. Shared files, memory, and browser state do not merge the Threads' conversation histories. Any broader memory sharing comes from explicitly authorized Service resource access, not from installing Claw or placing Claws in the same Workspace.

## Continuity and Ownership

Claw records the Project's resource references and its Thread associations. Service owns preparation, access, and lifecycle of the native resources. When a required resource is missing or unavailable, surface the affected Project and operation; an empty replacement cannot stand in for the original working state. Recovery or replacement is an explicit operator action. The ownership boundary also applies to [backup and restore](05-operator-workflows.md#backup-and-restore): restoring a Project reference does not restore the data behind it.
