import { createFileRoute } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { PageHeader } from "@/components/page-header";
import { Section } from "@/components/settings/section";
import { Badge } from "@/components/ui/badge";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemTitle,
} from "@/components/ui/item";
import { useServerSettings } from "@/api/settings";
import { useUserName } from "@/hooks/use-user-name";
import { useWorkspaces } from "@/hooks/use-workspaces";
import { keyPrefix } from "@/i18n";

const PROFILE_T = keyPrefix(($) => $.settings.profile);

export const Route = createFileRoute("/settings/profile")({ component: ProfilePage });

/** Who the user is signed in as, and the roles and workspaces the sign-in grants. */
function ProfilePage() {
  const { t } = useTranslation(undefined, PROFILE_T);
  const user = useServerSettings((settings) => settings.user);
  const name = useUserName();
  const workspaces = useWorkspaces();

  if (!user) return null;

  const facts = [
    { label: t(($) => $.name), value: user.name ?? name },
    { label: t(($) => $.email), value: user.email },
    { label: t(($) => $.id), value: user.id },
  ];

  return (
    <>
      <PageHeader title={t(($) => $.title)} description={t(($) => $.description)} />
      <Section title={name}>
        <ItemGroup>
          {facts.map(
            ({ label, value }) =>
              value && (
                <Item key={label} size="sm">
                  <ItemContent>
                    <ItemDescription>{label}</ItemDescription>
                    <ItemTitle className="break-all">{value}</ItemTitle>
                  </ItemContent>
                </Item>
              ),
          )}
          <Item size="sm">
            <ItemContent>
              <ItemDescription>{t(($) => $.roles)}</ItemDescription>
              <div className="flex flex-wrap gap-1">
                {user.roles.length > 0 ? (
                  user.roles.map((role) => (
                    <Badge key={role} variant="secondary">
                      {role}
                    </Badge>
                  ))
                ) : (
                  <ItemTitle>{t(($) => $.noRoles)}</ItemTitle>
                )}
              </div>
            </ItemContent>
          </Item>
        </ItemGroup>
      </Section>
      <Section title={t(($) => $.workspaces)}>
        <ItemGroup>
          {workspaces.map(({ scope, label, writable }) => (
            <Item key={scope} variant="outline" size="sm">
              <ItemContent>
                <ItemTitle>{label}</ItemTitle>
                <ItemDescription className="font-mono">{scope}</ItemDescription>
              </ItemContent>
              <ItemActions>
                <Badge variant="outline">
                  {writable ? t(($) => $.writable) : t(($) => $.readOnly)}
                </Badge>
              </ItemActions>
            </Item>
          ))}
        </ItemGroup>
      </Section>
    </>
  );
}
