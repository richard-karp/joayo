import type { Category, Place } from "@/types";
import { ALL_CATEGORIES } from "@/types";

/**
 * Category filtering is page-level state, shared by the map and every list, so a
 * category switched off disappears from both at once. The state is the set of
 * HIDDEN categories (empty = show everything), which lets the map's multi-toggle
 * chips and the single-select pills read and write the same value.
 */

/** Places whose category is switched on. Uncategorized rows are never hidden. */
export function visibleByCategory(places: Place[], hidden: Set<Category>): Place[] {
  if (hidden.size === 0) return places;
  return places.filter((p) => !p.category || !hidden.has(p.category as Category));
}

/** The one category still showing, or null when the set isn't narrowed to exactly one. */
export function soleVisibleCategory(hidden: Set<Category>): Category | null {
  const shown = ALL_CATEGORIES.filter((c) => !hidden.has(c));
  return shown.length === 1 ? shown[0] : null;
}

/** Single-select: narrow to `cat`, or clear when it's already the only one showing. */
export function isolateCategory(hidden: Set<Category>, cat: Category): Set<Category> {
  return soleVisibleCategory(hidden) === cat
    ? new Set()
    : new Set(ALL_CATEGORIES.filter((c) => c !== cat));
}

/** Multi-select: flip one category on/off, leaving the rest alone. */
export function toggleCategory(hidden: Set<Category>, cat: Category): Set<Category> {
  const next = new Set(hidden);
  if (next.has(cat)) next.delete(cat);
  else next.add(cat);
  return next;
}
