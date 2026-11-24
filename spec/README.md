# A13n Claw Specification

Claw is an IM-first App plugin for A13n Service. The [architecture overview](00-overview.md) defines its product and platform boundaries.

This specification describes the target Service App. The [Claw 0.1.0 specification](https://github.com/Wh1isper/a13n-claw/tree/release/a13n-claw-v0.1.0/spec) is the reference for released standalone behavior.

- [Inbox](01-inbox.md): user and scheduled-item admission, unread notices, opening items, capacity, and read history.
- [Main Thread](02-main-thread.md): unread notices, worker coordination, scheduled occurrence handling, native waits, and the idle heartbeat.
- [Connectors and replies](03-connectors-and-replies.md): connector input, Project CLI setup, native replies, and custom channels.
- [Project](04-project.md): persistent working context, agent composition, memory sharing, and resource continuity.
- [Operator workflows](05-operator-workflows.md): Console jobs, headless setup and maintenance, and recovery boundaries.
