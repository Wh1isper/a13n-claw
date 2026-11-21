# Connectors and Replies

## Input Boundary

A connector binds to a specific Claw. Built-in connectors and operator-provided integrations submit messages through the same [Inbox](01-inbox.md) path. An incoming message carries its content and source context, such as the sender, conversation, original message, and reply target when available. The connector checks admission against its configured source and sender rules before submitting content. Message content is data; it does not choose the Project's executable or credentials.

The Feishu connector accepts direct messages that pass its configured source and sender rules. In group chats it accepts only messages that explicitly mention the bot by default. An operator can configure a bound group to accept messages without a mention. This changes only the mention filter: source and sender rules still apply, and accepted messages enter the same Claw Inbox.

Feishu attachment input includes its available metadata and source reference. The agent fetches the attachment on demand using the platform CLI in the Project Environment; the connector does not prefetch it.

A connector can reflect Claw's Read state on the platform, for example with a reaction on the original Lark message. When a GitHub-origin message is accepted into the Inbox, the integration adds an eyes reaction to its source where supported. This acknowledges delivery into the Inbox, not opening or completing the work. Claw does not provide built-in GitHub notification polling or mark-read. Intake feedback and Inbox-full errors are connector behavior, separate from Main's replies and not requiring an agent turn.

## CLI-First Replies

Main chooses the reply target and platform operation. It runs the corresponding CLI in the Project Environment. A reply can address the original message, its conversation, or another explicitly selected destination available to the configured account. The operation and payload follow the platform: for example, Lark text, rich messages, or files; GitHub issue comments, pull request comments, or reviews. Claw does not flatten these operations into a common text-only send format. Worker Threads communicate with Main through the [Inbox](01-inbox.md), and Main can inspect their saved outcomes for publication.

The initial release supports GitHub and Lark/Feishu. Main uses their official CLIs, `gh` and `lark-cli`, and interprets the selected CLI's result.

## Project Setup

Workspace administrators configure a GitHub reply token or Lark App secret on a connector. Service encrypts the write-only credential. The GitHub intake service account is separate from the reply identity. Credential checks, connector intake, CLI installation, and successful replies are distinct facts.

On supported Linux Environments, Claw checks for the CLIs before Main runs. It reuses installed binaries and installs missing ones from the official latest release. The administrator can request an update for the bound Environment; it is applied before the next Main execution without starting a Run. An Environment replacement clears queued maintenance. A failed download or verification leaves the prior binary in place.

Administrators explicitly authorize reply credentials for the selected Environment. Rebinding the Environment revokes that selection. Main's connector tool chooses a specific connector and uses only its platform credential for the operation; the credential is not included in Run options, general shell defaults, or readable settings. For Lark the command receives a short-lived bot token. The Environment and code running in it must be trusted with platform credentials; the Main-only tool is a collaboration boundary, not isolation from other processes in the same Environment.

## Custom Channels

An operator can connect an unsupported platform without adding a built-in connector. Their integration delivers messages with source and reply context through the Claw input interface. A prepared Project Environment supplies the platform CLI, account access, and guidance Main needs to reply. The operator owns the custom integration and its platform feedback; Claw applies the same Inbox admission, read state, and wake behavior.

```mermaid
sequenceDiagram
    participant A as Built-in or custom integration
    participant I as Claw Inbox
    participant M as Main Thread
    participant E as Project CLI
    participant P as Destination platform
    A->>I: Submit message with source and reply context
    alt Inbox full
        I-->>A: Reject admission
    else Accepted
        I-->>A: Stored unread
        I-->>M: Wake or steer with unread preview
        M->>I: Open selected message
        I-->>M: Return content and reply context
        opt Main decides to reply
            M->>E: Request a platform-native operation
            E->>P: Execute with the configured account
            P-->>E: Return the available outcome
            E-->>M: Report outcome
        end
    end
```
