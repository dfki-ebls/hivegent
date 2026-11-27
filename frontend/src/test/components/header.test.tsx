import { fireEvent, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { expect, it, vi } from "vitest";

import { Header } from "@/components/header";
import { renderWithQueries } from "@/test/render";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ to, children, ...props }: ComponentProps<"a"> & { to: string }) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("@/components/job-tray", () => ({ JobTray: () => null }));

vi.mock("@/runtime-config", () => ({
  runtimeConfig: () => ({ oidc: null, docs_url: "/docs" }),
}));

vi.mock("@/oidc", () => ({
  useOidc: () => ({
    isUserLoggedIn: true,
    decodedIdToken: { given_name: "Ada", family_name: "Lovelace", email: "ada@example.com" },
    logout: vi.fn<() => void>(),
  }),
}));

it("opens the user menu with the settings and links its entries", async () => {
  renderWithQueries(<Header />);

  fireEvent.click(screen.getByRole("button", { name: "Ada Lovelace" }));

  expect(screen.getByText("ada@example.com")).toBeTruthy();
  expect(screen.getByRole("menuitem", { name: "Settings" }).getAttribute("href")).toBe("/settings");
  // The server says who is an admin, and these settings never loaded.
  expect(screen.queryByRole("menuitem", { name: "Administration" })).toBeNull();
  expect(screen.getByRole("menuitem", { name: "Documentation" }).getAttribute("href")).toBe(
    "/docs/en/",
  );
  expect(screen.getByRole("menuitem", { name: "Sign out" })).toBeTruthy();
});
