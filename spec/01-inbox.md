# Inbox

## Unread and Read

Each Claw has its own Inbox for incoming user messages, worker-authored messages, and worker terminal notices. Built-in connectors and the Claw input interface use the same Inbox. Like a mailbox, the Inbox shows an unread list with the source, sender, and a short preview. An accepted message is unread. A notice or list view does not mark it read or put its full contents into Main's conversation.

Main opens a selected message with `pop`. It receives the full message, and the message moves from Unread to Read. Main chooses the order and what to do with each message. Read means the message was opened, not that any work mentioned in it was accepted, completed, delegated, or replied to.

Main can reopen a read message while it is retained. Reopening a read message does not make it unread or generate another unread notice. Only unread messages keep the Inbox prompting loop active; read history does not prevent Main from going idle.

A connector can reflect the first open on the source platform, for example with a Lark reaction. The reaction means Read, not that the work finished. A failed reaction does not make the message unread. [GitHub-origin acceptance feedback](03-connectors-and-replies.md#input-boundary) is a separate event.

## Capacity and Retention

Each Claw can configure its unread and read limits independently. The unread limit defaults to 500 messages. When the unread list is at or above its configured limit, Claw rejects explicit user and worker messages without evicting an unread message, and returns an Inbox-full result to the sender. Rejected messages do not produce unread notices. Opening an unread message frees a slot.

A worker Run's automatic terminal notice is attempted once. If the Inbox is full, the notice is dropped without retry; Main can inspect the saved outcome through Service. There is no global priority queue. Main prefers user input when choosing work, without strict ordering or blocking worker messages.

The read limit defaults to 3,000 messages. Claw keeps read messages for up to seven days after they were first opened. When the configured read limit is exceeded, Claw removes the earliest-read messages first. Reopening does not extend retention. Cleanup removes that Inbox history without retaining a separate copy for deduplication or replies. It does not remove content already entered into a Thread's saved context; native context management remains under Service.

## Message Flow

```mermaid
flowchart TD
    Incoming[User or worker message or worker terminal notice] --> Check{Unread list full?}
    Check -->|Explicit message| Reject[Return full-Inbox error]
    Check -->|Terminal notice| Drop[Drop notice without retry]
    Check -->|Capacity available| Unread[Store as unread]
    Unread --> Preview[Wake or steer Main with short preview]
    Preview --> Open[Main opens a selected message]
    Open --> Read[Return full message and move it to Read]
    Read -->|Connector supports feedback| Feedback[Show read indicator on source]
    Read -->|Reopen| Read
    Read -->|Age or count limit| Prune[Remove from read history]
```
