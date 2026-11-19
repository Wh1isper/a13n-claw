# Bridges and External Delivery

## Ingress and Routing

Claw qualifies each sender and source by platform and connection, resolves a single Instance for each accepted event, and applies sender policy and mentions-only filtering before dispatch. Direct messages still require authorization. A repeated event returns its recorded disposition. Ignored messages do not enter the agent's context through quoted or surrounding history. Acknowledgement follows durable acceptance, not completion of execution.

One Thread mode routes work to the Instance's Main Inbox. Per-Channel mode keeps a conversation Thread per qualified Channel. Per-trigger mode starts an independent Thread per eligible event, including a new eligible reply; decision responses target their existing wait rather than creating a task. Channel and reply identity survive dispatch and reconfiguration. GitHub treats each issue or pull request as a conversation.

## Lark

Lark intake uses the bot's long connection. Conversation includes direct and group messages, topic or quoted context where authorized, and supported inbound image and file attachments retained for use by the selected work. Genuine mentions come from platform mention evidence. Users receive progress and decision prompts where permitted and can reply with controls for the exact operation. Feedback distinguishes receipt, processing, and completion; it does not replace saved execution or delivery facts. Feedback reactions describe an observed state of the exact message.

Claw can publish text and files to authorized conversations, preserving topic or reply context when the platform supports it. In One Thread mode, final answers and worker results are not automatically broadcast. In modes with automatic results enabled, an explicit reply suppresses a redundant automatic result for the same input.

## GitHub

Claw polls notifications from a named connection and handles eligible issue, pull request, and review-comment requests. Discovery retains a cursor, covers pagination and configured lookback and delay, and does not skip outstanding requests when a notification changes. Polling observes sampled state, not a complete event archive. It checks configured scope, allowed author, and an exact account mention, and ignores its own output. Only the eligible request enters context. Repeated or delayed notifications do not duplicate a task. Supported reactions and replies are recorded separately from intake. Mark-read, when enabled, waits for business settlement and associated submissions and deliveries, and verifies the notification version and current authority before clearing it. A failed mark-read does not repeat the task or comment. GitHub comments do not act as IM approvals.

## Delivery

Each external publication has explicit contents, source and destination. A topic fallback remains within the same authorized conversation and follows a definite rejection, not a timeout. Partial results identify what was sent. Confirmed, definitely unsent, and unknown results remain distinguishable; a timeout does not establish non-delivery. A binding change does not redirect a retained reply. Service completion neither publishes nor settles an external delivery by itself.
