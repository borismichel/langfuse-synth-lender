import type { Session } from "next-auth";

import { createApiSessionToken } from "@/lib/session-token";

export type UseCase = {
  id: string;
  title: string;
  description?: string | null;
};

type ApiUseCase = {
  id?: string | number;
  title?: string;
  name?: string;
  description?: string | null;
};

function apiBaseUrl(): string {
  return (process.env.PORTAL_API_BASE_URL || "http://localhost:8000").replace(
    /\/$/,
    "",
  );
}

function normalizeUseCases(payload: unknown): UseCase[] {
  if (!Array.isArray(payload)) {
    return [];
  }

  return payload.reduce<UseCase[]>((items, rawItem) => {
    const item = rawItem as ApiUseCase;
    const id = item.id === undefined ? undefined : String(item.id);
    const title = item.title || item.name;

    if (!id || !title) {
      return items;
    }

    items.push({
        id,
        title,
        description: item.description ?? null,
    });

    return items;
  }, []);
}

export async function fetchUseCases(session: Session): Promise<UseCase[]> {
  const token = await createApiSessionToken(session);
  const response = await fetch(`${apiBaseUrl()}/use-cases`, {
    headers: {
      Accept: "application/json",
      Authorization: `Bearer ${token}`,
    },
    cache: "no-store",
  });

  if (!response.ok) {
    throw new Error(`GET /use-cases failed with ${response.status}`);
  }

  return normalizeUseCases(await response.json());
}
