'use client';

import type { Place } from "@/types";
import { kakaoDirectionsUrl, kakaoMapUrl, naverMapUrl } from "@/lib/mapLinks";

/** Kakao / Directions / Naver deep-link buttons for a place. Renders nothing when
 *  the place has neither a POI id nor coordinates. */
export default function MapLinks({ place, className = "" }: { place: Place; className?: string }) {
  const kakao = kakaoMapUrl(place);
  const directions = kakaoDirectionsUrl(place);
  const naver = naverMapUrl(place);
  if (!kakao && !naver) return null;

  const base = "text-xs px-2 py-0.5 rounded border transition-colors whitespace-nowrap";
  return (
    <div className={`flex flex-wrap items-center gap-1.5 ${className}`}>
      {kakao && (
        <a href={kakao} target="_blank" rel="noopener noreferrer"
           className={`${base} border-yellow-300 bg-yellow-50 text-yellow-800 hover:bg-yellow-100`}>
          Kakao Map ↗
        </a>
      )}
      {directions && (
        <a href={directions} target="_blank" rel="noopener noreferrer"
           className={`${base} border-zinc-200 text-zinc-600 hover:border-zinc-400`}>
          Directions ↗
        </a>
      )}
      {naver && (
        <a href={naver} target="_blank" rel="noopener noreferrer"
           className={`${base} border-green-300 bg-green-50 text-green-800 hover:bg-green-100`}>
          Naver ↗
        </a>
      )}
    </div>
  );
}
