import { notFound } from "@tanstack/react-router";

import { HttpError } from "@/api/client";

/** The value of *promise*, or the router's not-found screen when the backend answered 404. */
export async function orNotFound<T>(promise: Promise<T>): Promise<T> {
  try {
    return await promise;
  } catch (error) {
    if (error instanceof HttpError && error.status === 404) {
      throw notFound();
    }

    throw error;
  }
}
