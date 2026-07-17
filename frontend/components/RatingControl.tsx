'use client';

import { useState } from "react";
import { ratePlace, setWantToGo } from "@/lib/api";
import type { Place, Rating } from "@/types";

const RATINGS: { value: Rating; label: string; title: string; active: string }[] = [
  { value: "down", label: "👎", title: "Bad", active: "bg-red-100 text-red-700" },
  { value: "up", label: "👍", title: "Good", active: "bg-green-100 text-green-700" },
  { value: "double", label: "👍👍", title: "Very good", active: "bg-green-100 text-green-700" },
];

interface Props {
  place: Place;
  onUpdate: (p: Place) => void;
  size?: "sm" | "md";
}

/**
 * Netflix-style 3-thumb rating (👎 / 👍 / 👍👍) plus a ☆ "want to go" wishlist star.
 * Rating a place marks it visited and clears its wishlist flag. Updates optimistically
 * and reverts on error.
 */
export default function RatingControl({ place, onUpdate, size = "md" }: Props) {
  const [loading, setLoading] = useState(false);
  const pad = size === "sm" ? "px-1 py-0.5 text-xs" : "px-2 py-0.5 text-sm";

  async function apply(optimistic: Place, call: () => Promise<Place>) {
    if (loading) return;
    setLoading(true);
    onUpdate(optimistic);            // optimistic
    try {
      onUpdate(await call());        // authoritative server state
    } catch {
      onUpdate(place);              // revert
    } finally {
      setLoading(false);
    }
  }

  function rate(v: Rating) {
    const next: Rating | null = place.my_rating === v ? null : v;
    // Rating (non-null) marks the place visited → drops it from "want to go".
    apply(
      { ...place, my_rating: next, want_to_go: next ? false : place.want_to_go },
      () => ratePlace(place.id, next),
    );
  }

  function toggleWish() {
    const next = !place.want_to_go;
    apply({ ...place, want_to_go: next }, () => setWantToGo(place.id, next));
  }

  return (
    <div className="flex items-center gap-1">
      {RATINGS.map((r) => (
        <button
          key={r.value}
          type="button"
          title={r.title}
          disabled={loading}
          onClick={() => rate(r.value)}
          className={`rounded transition-colors ${pad} ${
            place.my_rating === r.value ? r.active : "text-zinc-400 hover:text-zinc-700 hover:bg-zinc-100"
          }`}
        >
          {r.label}
        </button>
      ))}
      <span className="mx-0.5 self-stretch w-px bg-zinc-200" aria-hidden />
      <button
        type="button"
        title={place.want_to_go ? "On your want-to-go list" : "Want to go"}
        disabled={loading}
        onClick={toggleWish}
        className={`rounded transition-colors ${pad} ${
          place.want_to_go ? "text-amber-500" : "text-zinc-400 hover:text-amber-500 hover:bg-amber-50"
        }`}
      >
        {place.want_to_go ? "★" : "☆"}
      </button>
    </div>
  );
}
