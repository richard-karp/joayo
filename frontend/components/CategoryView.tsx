'use client';

import { Fragment } from "react";
import { Badge } from "@/components/ui/badge";
import CategoryPills from "@/components/CategoryPills";
import PlaceCard from "@/components/PlaceCard";
import { soleVisibleCategory, visibleByCategory } from "@/lib/categories";
import type { Category, Place } from "@/types";
import { CATEGORY_COLORS, CATEGORY_LABELS } from "@/types";

interface Props {
  /** Every place for the current server-side filters — pill counts are drawn from
   *  this unfiltered set, so a hidden category can still be switched back on. */
  places: Place[];
  expandedIds: string[];
  onPlaceClick: (placeId: string) => void;
  activeLabel: string | null;
  onLabelClick: (label: string) => void;
  hiddenCategories: Set<Category>;
  /** Single-select: a category to isolate, or null to show all. */
  onCategorySelect: (cat: Category | null) => void;
}

export default function CategoryView({
  places,
  expandedIds,
  onPlaceClick,
  activeLabel,
  onLabelClick,
  hiddenCategories,
  onCategorySelect,
}: Props) {
  const activeCategory = soleVisibleCategory(hiddenCategories);
  const expanded = new Set(expandedIds);

  const placePlaces = places.filter((p) => p.is_place);

  const filtered = visibleByCategory(placePlaces, hiddenCategories)
    .sort((a, b) => b.source_urls.length - a.source_urls.length);

  return (
    <div>
      <CategoryPills
        items={placePlaces}
        hiddenCategories={hiddenCategories}
        onCategorySelect={onCategorySelect}
      />

      {/* Place list */}
      <h2 className="text-sm font-semibold text-zinc-500 uppercase tracking-wide mb-3">
        {activeCategory
          ? CATEGORY_LABELS[activeCategory]
          : hiddenCategories.size > 0
          ? "Selected categories"
          : "All categories"}
      </h2>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-zinc-400 text-xs">
            <th className="text-left pb-2 font-medium">Place</th>
            {!activeCategory && (
              <th className="text-left pb-2 font-medium hidden sm:table-cell">Category</th>
            )}
            <th className="text-right pb-2 font-medium">Mentions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-100">
          {filtered.map((place) => {
            const isExpanded = expanded.has(place.id);
            return (
              <Fragment key={place.id}>
                <tr
                  className={`cursor-pointer transition-colors hover:bg-zinc-50 ${
                    isExpanded ? "bg-zinc-100" : ""
                  }`}
                  onClick={() => onPlaceClick(place.id)}
                >
                  <td className="py-2 font-medium text-zinc-900">
                    <span className="flex items-center gap-1.5">
                      <span
                        className={`text-zinc-400 text-[0.7rem] transition-transform ${isExpanded ? "rotate-90" : ""}`}
                        aria-hidden
                      >
                        ▸
                      </span>
                      <span className="truncate max-w-[200px]">{place.location_name}</span>
                    </span>
                    {place.city && (
                      <span className="block pl-4 text-xs text-zinc-400 font-normal">{place.city}</span>
                    )}
                  </td>
                  {!activeCategory && (
                    <td className="py-2 hidden sm:table-cell">
                      {place.category && (
                        <Badge className={`text-xs py-0 ${CATEGORY_COLORS[place.category as Category]}`}>
                          {CATEGORY_LABELS[place.category as Category]}
                        </Badge>
                      )}
                    </td>
                  )}
                  <td className="py-2 text-right tabular-nums text-zinc-500">
                    {place.source_urls.length}
                  </td>
                </tr>
                {isExpanded && (
                  <tr>
                    <td colSpan={activeCategory ? 2 : 3} className="p-0">
                      <PlaceCard
                        place={place}
                        className="mb-3 mt-1"
                        activeLabel={activeLabel}
                        onLabelClick={onLabelClick}
                        onClose={() => onPlaceClick(place.id)}
                      />
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
