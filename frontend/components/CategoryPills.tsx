'use client';

import type { Category, Place } from "@/types";
import { ALL_CATEGORIES, CATEGORY_LABELS } from "@/types";

interface Props {
  /** The UNFILTERED items this row counts. Counts must come from the unfiltered set
   *  so a category that is currently hidden still shows a count and can be switched
   *  back on — filtering first would drop its pill to zero and then out of the row. */
  items: Place[];
  hiddenCategories: Set<Category>;
  /** Single-select: a category to isolate, or null to show all. */
  onCategorySelect: (cat: Category | null) => void;
}

/** The category pill row. Shared by the Categories and Things views so the two read
 *  the same `hiddenCategories` state the map chips write to — a view that filters by
 *  category but renders no control leaves the user with no way to see or undo it. */
export default function CategoryPills({ items, hiddenCategories, onCategorySelect }: Props) {
  const countsByCategory = ALL_CATEGORIES.reduce<Record<string, number>>((acc, cat) => {
    acc[cat] = items.filter((p) => p.category === cat).length;
    return acc;
  }, {});

  return (
    <div className="flex items-center gap-2 flex-wrap mb-4">
      <button
        onClick={() => onCategorySelect(null)}
        className={`px-3 py-1 rounded-full text-xs font-medium border transition-colors ${
          hiddenCategories.size === 0
            ? "bg-zinc-900 text-white border-zinc-900"
            : "bg-white text-zinc-600 border-zinc-200 hover:border-zinc-400"
        }`}
      >
        All
        <span className="ml-1 opacity-60">{items.length}</span>
      </button>
      {ALL_CATEGORIES.filter((cat) => countsByCategory[cat] > 0).map((cat) => {
        // Nothing hidden → the flat "All is selected" look. Once anything is
        // hidden (here or via the map chips) each pill shows its own on/off
        // state, so a multi-category selection made on the map reads correctly.
        const hidden = hiddenCategories.has(cat);
        const on = hiddenCategories.size > 0 && !hidden;
        return (
          <button
            key={cat}
            onClick={() => onCategorySelect(cat)}
            className={`px-3 py-1 rounded-full text-xs font-medium border transition-colors ${
              on ? "bg-zinc-900 text-white border-zinc-900" : "bg-white border-zinc-200 hover:border-zinc-400"
            } ${hidden ? "text-zinc-400 line-through" : on ? "" : "text-zinc-600"}`}
          >
            {CATEGORY_LABELS[cat]}
            <span className="ml-1 opacity-60">{countsByCategory[cat]}</span>
          </button>
        );
      })}
    </div>
  );
}
