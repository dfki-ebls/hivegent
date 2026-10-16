import { fireEvent, render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { expect, it, vi } from "vitest";

import { Header } from "@/components/Header";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ to, children, ...props }: ComponentProps<"a"> & { to: string }) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("@/components/JobTray", () => ({ JobTray: () => null }));

vi.mock("@/oidc", () => ({
  useOidc: () => ({
    isUserLoggedIn: true,
    decodedIdToken: { given_name: "Ada", family_name: "Lovelace", email: "ada@example.com" },
    logout: vi.fn<() => void>(),
  }),
}));

it("opens the user menu with the account and links its entries", async () => {
  render(<Header />);

  fireEvent.click(screen.getByRole("button", { name: "Ada Lovelace" }));

  expect(await screen.findByText("ada@example.com")).toBeTruthy();
  expect(screen.getByRole("menuitem", { name: "Account" }).getAttribute("href")).toBe(
    "/settings/account",
  );
  expect(screen.getByRole("menuitem", { name: "Sign out" })).toBeTruthy();
});
