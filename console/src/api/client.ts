import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

export type Schema = components["schemas"];
export type FieldError = { path: (string | number)[]; code: string };
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly fields: FieldError[] = [],
  ) {
    super(message);
  }
}

export function createApi(token: string, unauthorized: () => void) {
  const client = createClient<paths>({
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  client.use({
    async onResponse({ response }) {
      if (response.status === 401) unauthorized();
      if (!response.ok) {
        const payload = await response
          .clone()
          .json()
          .catch(() => null);
        throw new ApiError(
          response.status,
          payload?.error?.code ?? "http_error",
          payload?.error?.message ?? `Request failed (${response.status})`,
          payload?.error?.fields ?? [],
        );
      }
    },
  });
  return client;
}
export type Api = ReturnType<typeof createApi>;

// HTTP errors are raised by the client middleware; typed successful payloads flow unchanged.
export async function data<T>(request: Promise<{ data?: T }>): Promise<T> {
  const response = await request;
  return response.data as T;
}
