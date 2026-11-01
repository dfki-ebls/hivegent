import { isNotFound } from "@tanstack/react-router";
import { expect, it } from "vitest";

import { HttpError } from "@/api/client";
import { orNotFound } from "@/lib/not-found";

it("turns a 404 into the router's not-found and passes other failures on", async () => {
  await expect(orNotFound(Promise.resolve([]))).resolves.toEqual([]);
  await expect(orNotFound(Promise.reject(new HttpError("gone", 404)))).rejects.toSatisfy(
    isNotFound,
  );
  await expect(orNotFound(Promise.reject(new HttpError("down", 500)))).rejects.toBeInstanceOf(
    HttpError,
  );
});
