# Persistence and Recovery

## Ownership and Recovery

Claw owns its App tables and migrations for connections, bindings, qualified events, work and delivery records, native references, and schedule associations. Service persists the native execution and resources. Claw business state does not live in Service metadata; Claw does not copy native checkpoints or create its own Run history.

An external acceptance survives restart. Pending work can advance without another message arriving. Claw reconciles a lost native response using the original request identity and receipt before treating work as newly admissible. A delivery attempt distinguishes confirmed delivery, known failure, and unknown outcome. An unknown outcome stays visible until reconciled; repeating the agent is not a delivery recovery operation.

## Retention, Restore, and Migration

Retention starts with a preview. Operators can back up the deployment’s consistent durable state and restore it independently of the live deployment. A usable backup includes the related Claw and Service records, configured durable files and working data, and the keys required to read them. Retention preserves unresolved obligations and their recovery evidence; product cleanup does not delete shared memory or unrelated native history.

The embedded Host release and the Service App are separate runtime formats. Migration accounts for configured connections, bindings, memory, files, schedules, and outstanding work and effects. Historical execution is not made resumable by copying its transcript. Old data is kept available for recovery or reference until migration is verified. For a connection or schedule being transferred, only one deployment admits work and sends results at a time. Restoring old state does not reverse effects already observed by an external platform.
