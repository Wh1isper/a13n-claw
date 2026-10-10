import { useState } from "react";
import { Api, requestId, type Resource, type Thread } from "./api";
import { Action, ErrorNotice } from "./components";
import { useCommand } from "./commands";

export function NewThread({
  api,
  profiles,
  done,
  cancel,
}: {
  api: Api;
  profiles: Resource[];
  done: (id: string) => void;
  cancel: () => void;
}) {
  const [title, setTitle] = useState("");
  const [profile, setProfile] = useState("");
  const command = useCommand<
    { request_id: string; title: string; profile_id: string | null },
    Thread
  >("create-thread");
  return (
    <section className="panel form-panel">
      <p className="eyebrow">INDEPENDENT HISTORY · SHARED WORKSPACE</p>
      <h1>New conversation</h1>
      <label>
        Title
        <input
          value={command.payload?.title ?? title}
          disabled={!!command.payload}
          placeholder="What are you working on?"
          onChange={(event) => setTitle(event.target.value)}
          autoFocus
        />
      </label>
      <label>
        Profile
        <select
          value={command.payload?.profile_id ?? profile}
          disabled={!!command.payload}
          onChange={(event) => setProfile(event.target.value)}
        >
          <option value="">Instance default</option>
          {profiles.map((item) => (
            <option key={item.id}>{item.id}</option>
          ))}
        </select>
      </label>
      <p className="subtle">
        Creating a Thread does not start execution. Send its first message when
        you're ready.
      </p>
      <ErrorNotice>{command.error}</ErrorNotice>
      <div className="toolbar">
        <Action
          disabled={
            command.busy ||
            (!command.payload && (!title.trim() || !profiles.length))
          }
          run={async () => {
            const thread = await command.execute(
              () => ({
                request_id: requestId(),
                title,
                profile_id: profile || null,
              }),
              (value) => api.send<Thread>("/threads", value),
            );
            if (thread) done(thread.id);
          }}
        >
          {command.result
            ? "Open created Thread"
            : command.payload
              ? "Retry same creation"
              : "Create Thread"}
        </Action>
        {command.result && (
          <button
            onClick={() => {
              command.clear();
              setTitle("");
              setProfile("");
            }}
          >
            Start another Thread
          </button>
        )}
        <button onClick={cancel}>Close</button>
      </div>
    </section>
  );
}
