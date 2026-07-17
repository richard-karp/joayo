'use client';

import { useState } from "react";
import { reviewPlace, type ReviewAction } from "@/lib/api";
import type { Place } from "@/types";

interface Props {
  place: Place;
  onUpdate: (p: Place) => void;
}

/**
 * Remediation panel for a low-confidence ("best guess") pin. Shown only while
 * `place.needs_review` is set; disappears once the reviewer confirms, re-locates,
 * or removes the pin. A failed re-locate (no Kakao match) shows the reason inline
 * and leaves the pin untouched.
 */
export default function ReviewPanel({ place, onUpdate }: Props) {
  const [name, setName] = useState(place.native_name ?? "");
  const [loading, setLoading] = useState<ReviewAction | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!place.needs_review) return null;

  async function run(action: ReviewAction, nativeName?: string) {
    if (loading) return;
    setLoading(action);
    setError(null);
    try {
      onUpdate(await reviewPlace(place.id, action, nativeName));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
    } finally {
      setLoading(null);
    }
  }

  const busy = loading !== null;

  return (
    <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 space-y-3">
      <div>
        <p className="text-sm font-semibold text-amber-900">⚠ Location is a best guess</p>
        <p className="text-xs text-amber-700 mt-0.5">
          This pin was auto-matched with low confidence. Confirm it, re-locate it with
          the correct Korean name, or remove the pin.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => run("confirm")}
          className="rounded-md bg-green-600 text-white text-sm px-3 py-1.5 hover:bg-green-700 disabled:opacity-50"
        >
          {loading === "confirm" ? "Saving…" : "✓ Looks right"}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => run("reject")}
          className="rounded-md border border-zinc-300 bg-white text-zinc-700 text-sm px-3 py-1.5 hover:bg-zinc-50 disabled:opacity-50"
        >
          {loading === "reject" ? "Removing…" : "✕ Remove pin"}
        </button>
      </div>

      <div className="pt-2 border-t border-amber-100 space-y-1.5">
        <label htmlFor="regeocode-name" className="block text-xs text-amber-800">
          Or re-locate with the correct Korean (한글) name:
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <input
            id="regeocode-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="예: 수아한의원"
            className="flex-1 min-w-[10rem] rounded-md border border-zinc-300 bg-white px-2 py-1.5 text-sm text-zinc-900"
          />
          <button
            type="button"
            disabled={busy || !name.trim()}
            onClick={() => run("regeocode", name.trim())}
            className="rounded-md bg-amber-600 text-white text-sm px-3 py-1.5 hover:bg-amber-700 disabled:opacity-50"
          >
            {loading === "regeocode" ? "Locating…" : "Re-locate"}
          </button>
        </div>
      </div>

      {error && <p className="text-xs text-red-600">{error}</p>}
    </div>
  );
}
