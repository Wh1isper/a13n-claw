# Service Integration

## App Boundary

Claw is installed as an in-process Service App with its own business state, adapters, operator operations, product tools, and A13n Console contribution. App installation and Service enablement are distinct from a configured Instance and its external grants. A disabled App does not start new product work. Accepted native execution remains subject to the Service lifecycle.

Claw calls supported Service operations directly. A native route and the App use the same native semantics for resource selection, admission, execution, collaboration, scheduling, and control. A product tool acts in the context of its actual authorized execution and selected Instance. Claw owns qualified external effects, not a second execution authority.

## Integration Requirements

When Service lacks a shared capability needed by released Claw workflows, that capability belongs in its owning A13n subsystem. Claw specializes messaging and its business policy; it does not substitute a second scheduler, worker runtime, memory stack, or provider integration for native platform behavior.

A full integration preserves qualified routing across Instances, durable acceptance and exact recovery, persistent worker collaboration, native decisions and retained tool controls, external publication, queued scheduled work, operating without a browser, and durable working context. The contract describes those outcomes rather than new upstream interfaces or how Service implements them.
