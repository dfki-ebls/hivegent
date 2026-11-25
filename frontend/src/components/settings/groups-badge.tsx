import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";

/** The groups something configured on the host serves, or everyone without any. */
export function GroupsBadge({ groups }: { groups: readonly string[] }) {
  const { t } = useTranslation();

  return (
    <Badge variant="outline">
      {groups.length > 0 ? groups.join(", ") : t(($) => $.settings.admin.everyone)}
    </Badge>
  );
}
