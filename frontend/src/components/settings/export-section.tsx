import { useMutation } from "@tanstack/react-query";
import { DownloadIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { EXPORT_PARTS, type ExportPart, exportUserData } from "@/api/account";
import { Section } from "@/components/settings/section";
import { Button } from "@/components/ui/button";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemTitle,
} from "@/components/ui/item";
import { Spinner } from "@/components/ui/spinner";
import { keyPrefix } from "@/i18n";

const EXPORT_T = keyPrefix(($) => $.settings.account.export);

/** Downloads of the user's data, all of it first, then each part on its own. */
export function ExportSection() {
  const { t } = useTranslation(undefined, EXPORT_T);
  const download = useMutation({ mutationFn: exportUserData });

  return (
    <Section icon={DownloadIcon} title={t(($) => $.title)} description={t(($) => $.description)}>
      <ItemGroup>
        {[undefined, ...EXPORT_PARTS].map((part?: ExportPart) => (
          <Item key={part ?? "all"} variant="outline" size="sm">
            <ItemContent>
              <ItemTitle>{t(($) => $.parts[part ?? "all"].label)}</ItemTitle>
              <ItemDescription>{t(($) => $.parts[part ?? "all"].description)}</ItemDescription>
            </ItemContent>
            <ItemActions>
              <Button
                variant="outline"
                size="sm"
                disabled={download.isPending}
                onClick={() => download.mutate(part)}
              >
                {download.isPending && download.variables === part ? (
                  <Spinner data-icon="inline-start" />
                ) : (
                  <DownloadIcon data-icon="inline-start" />
                )}
                {t(($) => $.download)}
              </Button>
            </ItemActions>
          </Item>
        ))}
      </ItemGroup>
    </Section>
  );
}
