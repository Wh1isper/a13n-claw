# Repository and Product Boundary

## Product Boundary

The [Service App specification](README.md) defines the target. Claw remains an independent product and distribution, starting from the A13n App scaffold. Its package owns the messaging business logic and its Console contribution. The documentation site is independent of the runtime frontend. A13n owns the runtime and its native platform contracts.

### Source Implementation and Release Availability

Until the replacement is implemented, this repository's runtime remains the embedded Harness UI Host. Tagged releases describe their own behavior. The [Claw 0.1.0 specification](https://github.com/Wh1isper/a13n-claw/tree/release/a13n-claw-v0.1.0/spec) is the behavioral baseline. The Service App target does not itself establish feature availability, package compatibility, or a completed migration.

## Distribution and Compatibility

Claw and Service share a major/minor release line and maintain independent patch numbers. A Claw `0.2.x` release develops and validates against one exact Service `0.2.y` release, not any patch of that line. The Claw image derives from the exact published Service image and includes the Claw App and its Console assets. Workspace environments and browser resources retain independent lifecycles.

Service owns native resource and saved-state compatibility. Claw owns its App data, external bindings, and operator workflows. [Persistence and recovery](04-persistence-and-recovery.md) defines the boundary for transfer from the embedded Host and for existing data. The released behavior is the migration target; the old runtime is not a fallback in the new product.
