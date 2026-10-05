import { LANGUAGE } from "@/i18n";

const integer = new Intl.NumberFormat(LANGUAGE);
const decimal = new Intl.NumberFormat(LANGUAGE, {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});
const relative = new Intl.RelativeTimeFormat(LANGUAGE, { numeric: "auto", style: "short" });
const date = new Intl.DateTimeFormat(LANGUAGE, { dateStyle: "medium" });
const dateTime = new Intl.DateTimeFormat(LANGUAGE, { dateStyle: "medium", timeStyle: "short" });

/** A number with the interface language's grouping, e.g. `1,234` or `1.234`. */
export function formatNumber(value: number): string {
  return integer.format(value);
}

/** A number with exactly one decimal, e.g. `12.5` or `12,5`. */
export function formatDecimal(value: number): string {
  return decimal.format(value);
}

/** A byte count as a human-readable file size, e.g. `1.5 KB` or `1,5 KB`. */
export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${integer.format(bytes)} B`;
  if (bytes < 1024 * 1024) return `${decimal.format(bytes / 1024)} KB`;
  return `${decimal.format(bytes / (1024 * 1024))} MB`;
}

/** A calendar date, e.g. `Oct 5, 2026` or `05.10.2026`. */
function formatDate(value: string | Date): string {
  return date.format(new Date(value));
}

/** A date with its time of day. */
export function formatDateTime(value: string | Date): string {
  return dateTime.format(new Date(value));
}

const UNITS: ReadonlyArray<[Intl.RelativeTimeFormatUnit, number]> = [
  ["second", 60],
  ["minute", 60],
  ["hour", 24],
  ["day", 7],
  ["week", 30 / 7],
];

/** How long ago *value* was in the largest fitting unit (`now`, `5 min. ago`, `yesterday`), a plain date beyond a month. */
export function formatRelativeTime(value: string | Date): string {
  let amount = (Date.now() - new Date(value).getTime()) / 1000;

  for (const [unit, size] of UNITS) {
    if (amount < size) {
      return unit === "second"
        ? relative.format(0, unit)
        : relative.format(-Math.floor(amount), unit);
    }

    amount /= size;
  }

  return formatDate(value);
}
