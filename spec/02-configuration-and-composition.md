# Configuration and Composition

## Ownership

Service owns workspaces, agents, models and accounts, credentials, skills, tools, environments, memory, and the execution settings each Run accepts. Claw selects these resources for an Instance and owns its external connections, bindings, sender policy, routing mode, and publication and notification preferences. Product tools use the selected Instance and the caller's actual execution context.

## Setup and Changes

An operator enables the App under Service policy, creates or selects an Instance, chooses its working resources, and binds connections and allowed senders. Operators can validate and apply product configuration without a browser, reload schedule definitions, and inspect the effective bindings and resource availability. Configuration edits preserve the operator’s working copy on a version conflict. Changes distinguish Claw policy from Service resource edits; secrets are managed by their credential owner and excluded from ordinary output and config exports.

Resource and routing changes govern new admission. Changes do not rewrite an accepted Run, move a retained request to another Instance, or retarget an existing delivery. Revoked authority blocks new actions under the former grant. Switching routing modes leaves existing conversations, memory bindings, and outstanding work in their original contexts; any reassignment is explicit. A missing resource is shown as unavailable, not silently replaced.
