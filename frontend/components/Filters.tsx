'use client';

import { Badge } from "@/components/ui/badge";
import type { Category } from "@/types";
import { CATEGORY_LABELS } from "@/types";

interface Props {
  activeCategory: Category | null;
  onCategoryChange: (cat: Category | null) => void;
  authorFilter: string | null;
  onAuthorFilterClear: () => void;
}

const CATEGORIES: Category[] = ["eat", "see_visit", "do", "shop", "guide"];

export default function Filters({ activeCategory, onCategoryChange, authorFilter, onAuthorFilterClear }: Props) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <button
        onClick={() => onCategoryChange(null)}
        className={`rounded-full px-3 py-1 text-sm font-medium border transition-colors ${
          activeCategory === null
            ? "bg-zinc-900 text-white border-zinc-900"
            : "bg-white text-zinc-600 border-zinc-200 hover:border-zinc-400"
        }`}
      >
        All
      </button>
      {CATEGORIES.map((cat) => (
        <button
          key={cat}
          onClick={() => onCategoryChange(activeCategory === cat ? null : cat)}
          className={`rounded-full px-3 py-1 text-sm font-medium border transition-colors ${
            activeCategory === cat
              ? "bg-zinc-900 text-white border-zinc-900"
              : "bg-white text-zinc-600 border-zinc-200 hover:border-zinc-400"
          }`}
        >
          {CATEGORY_LABELS[cat]}
        </button>
      ))}

      {authorFilter && (
        <Badge
          variant="outline"
          className="cursor-pointer gap-1 pl-2 pr-1"
          onClick={onAuthorFilterClear}
        >
          @{authorFilter}
          <span className="text-zinc-400 hover:text-zinc-800">✕</span>
        </Badge>
      )}
    </div>
  );
}
