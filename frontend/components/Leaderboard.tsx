'use client';

import { useEffect, useState } from "react";
import { getLeaderboard } from "@/lib/api";
import type { LeaderboardEntry } from "@/types";

interface Props {
  activeAuthor: string | null;
  activeCategory?: string | null;
  onAuthorClick: (username: string) => void;
  onCreatorSelect?: (username: string) => void;
}

const PAGE_SIZE = 20;

export default function Leaderboard({ activeAuthor, activeCategory, onAuthorClick, onCreatorSelect }: Props) {
  const [entries, setEntries] = useState<LeaderboardEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    getLeaderboard(activeCategory)
      .then((data) => {
        const sorted = [...data].sort(
          (a, b) => b.attributed_count - a.attributed_count || b.mentioned_count - a.mentioned_count
        );
        setEntries(sorted);
        setShowAll(false);
        setLoading(false);
      });
  }, [activeCategory]);

  if (loading) {
    return (
      <div className="space-y-2 animate-pulse">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-8 bg-zinc-100 rounded" />
        ))}
      </div>
    );
  }

  if (entries.length === 0) {
    return <p className="text-sm text-zinc-400">No creators yet.</p>;
  }

  const visible = showAll ? entries : entries.slice(0, PAGE_SIZE);

  return (
    <div>
      <h2 className="text-sm font-semibold text-zinc-500 uppercase tracking-wide mb-3">
        Creators
      </h2>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-zinc-400 text-xs">
            <th className="text-left pb-2 font-medium">#</th>
            <th className="text-left pb-2 font-medium">Creator</th>
            <th className="text-right pb-2 font-medium">Places</th>
            <th className="text-right pb-2 font-medium">Mentions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-100">
          {visible.map((entry, i) => (
            <tr
              key={entry.username}
              className={`cursor-pointer transition-colors hover:bg-zinc-50 ${
                activeAuthor === entry.username ? "bg-zinc-100" : ""
              }`}
              onClick={() => {
                onAuthorClick(entry.username);
                onCreatorSelect?.(entry.username);
              }}
            >
              <td className="py-2 pr-2 text-zinc-400">{i + 1}</td>
              <td className="py-2 font-medium text-zinc-900">
                {(() => {
                  // Prefer handle extracted from profile_url, then username if it looks like a handle
                  const handleFromUrl = entry.profile_url
                    ? entry.profile_url.replace(/\/$/, "").split("/").pop()
                    : null;
                  const handle =
                    handleFromUrl && /^[\w.]+$/.test(handleFromUrl)
                      ? handleFromUrl
                      : /^[\w.]+$/.test(entry.username)
                      ? entry.username
                      : null;
                  const link = entry.profile_url ?? (handle ? `https://www.instagram.com/${handle}/` : null);
                  const display = handle ? `@${handle}` : entry.username;
                  return link ? (
                    <a
                      href={link}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="hover:underline text-blue-600"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {display}
                    </a>
                  ) : (
                    <span className="text-zinc-500 text-xs">{entry.username}</span>
                  );
                })()}
              </td>
              <td className="py-2 text-right text-zinc-900 tabular-nums font-medium">
                {entry.attributed_count}
              </td>
              <td className="py-2 text-right text-zinc-500 tabular-nums">
                {entry.mentioned_count}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {entries.length > PAGE_SIZE && (
        <button
          onClick={() => setShowAll((v) => !v)}
          className="mt-2 text-xs text-zinc-400 hover:text-zinc-700 transition-colors"
        >
          {showAll ? "Show less" : `Show all ${entries.length} creators`}
        </button>
      )}
    </div>
  );
}
