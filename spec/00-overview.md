# Product and Architecture Overview

## Product Position

Claw brings interactive agent work to messaging platforms. Users start work, add context, work with persistent workers, and receive results and files through IM. A13n Console and headless clients are operator surfaces for inspecting progress, responding to native questions and approvals, and stopping a selected operation.

One Claw App installation manages multiple Claws in the same Service deployment. Each Claw has its own connector configuration, one Project, and one Main Thread that coordinates its worker Threads. A connector is configured for a Claw; Claw does not select an assistant from message content. Each Claw keeps its own Inbox and heartbeat. Selecting the same Service agent does not join two Claws' conversations.

## Architecture

```mermaid
flowchart TB
    Platforms[Messaging platforms]
    Clients[Headless clients]
    subgraph Deployment[A13n Service deployment]
        Console[A13n Console with Claw pages]
        App[Claw App plugin]
        subgraph A[One Claw]
            Connector[Connectors and Claw input]
            Inbox[Inbox]
            Main[Main Thread]
            Workers[Worker Threads]
            Heartbeat[Idle heartbeat]
            Connector --> Inbox
            Inbox -->|Wake or steer with unread preview| Main
            Main -->|Open a selected message| Inbox
            Main -->|Delegate| Workers
            Heartbeat -->|Wake Main| Main
        end
        B[Other Claws]
        Service[Service execution and resources]
        Project[Claw Project]
        Work[Service Environment and optional Browser]
        Console --> App
        App --> A
        App --> B
        App -->|In-process operations| Service
        Main -->|Use reply CLIs| Work
        Main --> Project
        Workers --> Project
        Project --> Work
        Service --> Work
    end
    Platforms -->|Incoming messages| Connector
    Connector -->|Intake feedback| Platforms
    Work -->|Platform-native replies| Platforms
    Clients --> Connector
```

Claw owns the Project, connector, Inbox, Main and worker associations, and the heartbeat condition. Service owns the native Threads, Entries and Runs, workspace and resource authorization, and resource lifecycles. Claw stores its business state and references to native identities in its AppDatabase.

## Project

Each Claw's [Project](04-project.md) gives Main and its workers a shared persistent working context. The [operator workflows](05-operator-workflows.md) cover setup, inspection, and recovery in A13n Console and headless operation.

## End-to-End Flow

The connector addresses a specific Claw. The [Inbox](01-inbox.md) owns acceptance, unread previews, and read history. The [Main Thread](02-main-thread.md) owns the choice of what to read, handle, or delegate, including scheduled work. Service executes Main and its worker Threads in their Project context. Opening a message, completing work, and sending a reply are separate events.

```mermaid
sequenceDiagram
    participant U as Lark user
    participant C as Claw connector
    participant I as Claw Inbox
    participant M as Main Thread
    participant W as Managed worker Thread
    participant X as Reply CLI in the Project
    participant P as Platform API
    U->>C: Send a message
    C->>I: Offer fan-in to the configured Claw
    alt Unread capacity exhausted
        I-->>C: Not accepted
        C-->>U: Inbox full
    else Accepted
        I-->>C: Stored as unread
        I-->>M: Wake or attempt immediate steer with a preview
        M->>I: Open the selected message
        I-->>M: Body, now marked Read
        opt Connector provides read feedback
            I-->>C: Message opened
            C-->>U: Read indicator
        end
        opt Main delegates part of the work
            M->>W: Assign work in the Project
            W-->>M: Report the outcome
        end
        opt Main chooses to reply
            M->>X: Reply to the selected destination
            X->>P: Send in the platform format
            P-->>U: Deliver the reply
        end
    end
```

## Integration and Distribution

Claw uses A13n's App scaffold and contributes its frontend directly to A13n Console. It uses Service's supported in-process operations rather than maintaining a separate execution runtime. Shared capabilities belong in their A13n subsystem; Claw owns the messaging and operator workflows that use them.

Claw and Service share a major/minor release line with independent patch numbers. Claw develops and validates against one exact Service version in that line. Its release image derives from that published Service image and includes the Claw App and Console contribution.
