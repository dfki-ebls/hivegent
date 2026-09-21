# sbomqs currently evaluates every package as a physical component. This
# pipeline emits package-level logical components, for which BSI TR-03183-2
# section 3.2.2 excludes fields that only describe a physical file.
(if $logicalComponents then
   [
     "filename",
     "hash of deployable component",
     "executable property",
     "archive property",
     "structured property"
   ]
 else
   []
 end) as $physicalFields

# One walk of the document, so the lookup below costs nothing per section.
| (reduce ($bom[0] | .. | objects | select(has("licenses"))) as $component
    ({}; .["\($component.name?)-\($component.version?)"] += $component.licenses)
  ) as $licensesByElement

# sbomqs 2.1.2 extracts SPDX exceptions as if they were standalone licences,
# then rejects otherwise valid WITH expressions. Keep the external result for
# every other licence and accept this narrowly identifiable parser limitation.
| def known_checker_limitation:
    {
      "original licence (declared)": "declared",
      "distribution licence (concluded)": "concluded",
    }[.section_data_field] as $acknowledgement
    | $acknowledgement != null
      and any(
        $licensesByElement[.element_id][]?;
        .acknowledgement == $acknowledgement
          and ((.expression // "") | contains(" WITH "))
      );

  .sections[]
  | select(.required and .score < 10)
  | select(.section_id | IN("4", "5.2.1", "5.2.2"))
  | select(.section_data_field | IN($physicalFields[]) | not)
  | select(known_checker_limitation | not)
  | "\(.section_id) \(.section_data_field): \(.element_id)"
