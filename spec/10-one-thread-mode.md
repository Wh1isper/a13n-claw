# One Thread Mode

## Main and Workers

One Thread is the default routing mode. Each Instance in this mode has one canonical Main Thread. Main processes eligible business input, coordinates native workers, and can explicitly publish to an authorized Channel. A new Run does not replace Main's identity. Other Instances maintain their own Main and outstanding work.

A worker is a persistent native conversation owned by Main. Main can assign work, inspect progress, and receive directed questions and results across Run boundaries. An authorized operator can address a worker directly; Main gets an attention item, not a duplicate assignment. A worker has no Claw publication authority. Its output returns to the collaboration context, where Main can choose what to publish. A live notification is not evidence that a worker result was saved.

## Inbox and Settlement

The durable Inbox lets Main inspect retained input and explicitly mark it handled, ignored, or deferred. Deferred work resumes only on the matching dependency or authorized action. A notice, Service receipt, completed Run, successful send, or cancellation does not by itself settle the item.

Pending obligations are rediscovered after restart and can become actionable without another external message. Work waiting on a decision or uncertain effect is visible without blind reprocessing. An unavailable Main or worker is shown to the operator; work is not silently handed to a different conversation.
