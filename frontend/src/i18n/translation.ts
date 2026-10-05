/**
 * The shape another language must match: every key of the English catalog,
 * with any string in place of each English literal. Used as
 * `satisfies Translation<typeof en.section>`, so a missing or extra key in a
 * translation fails the type check.
 */
export type Translation<T> = {
  readonly [K in keyof T]: T[K] extends string ? string : Translation<T[K]>;
};
