import { useQuery } from "@tanstack/react-query";
import { SparklesIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { skillCommand, skillQueries } from "@/api/skills";
import { Button } from "@/components/ui/button";
import { InputGroupAddon } from "@/components/ui/input-group";

/** The most suggestions shown at once. */
const MAX_SUGGESTIONS = 6;

/**
 * The skills a draft of `/<query>` may invoke, each completing the draft when
 * pressed with its reference, which no skill of another scope shares. The
 * server loads the skill a message starts with `/<reference>` or `/<name>` for.
 */
export function SkillSuggestions({
  query,
  onPick,
}: {
  query: string;
  onPick: (command: string) => void;
}) {
  const { t } = useTranslation();
  const { data: skills = [] } = useQuery(skillQueries.list());
  const matches = skills
    .filter(
      (skill) =>
        skill.user_invocable &&
        (skill.name.startsWith(query) || skillCommand(skill).startsWith(query)),
    )
    .slice(0, MAX_SUGGESTIONS);

  if (matches.length === 0) return null;

  return (
    <InputGroupAddon
      align="block-start"
      className="flex-wrap"
      aria-label={t(($) => $.chat.composer.skillSuggestions)}
    >
      {matches.map((skill) => (
        <Button
          key={skill.ref}
          type="button"
          variant="outline"
          size="xs"
          title={skill.description ?? undefined}
          onClick={() => onPick(skillCommand(skill))}
        >
          <SparklesIcon data-icon="inline-start" />/{skillCommand(skill)}
        </Button>
      ))}
    </InputGroupAddon>
  );
}
