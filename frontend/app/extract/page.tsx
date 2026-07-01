'use client';

import Link from "next/link";
import { useCallback, useState } from "react";
import JobStatus from "@/components/JobStatus";
import UploadCard from "@/components/UploadCard";

export default function ExtractPage() {
  const [jobId, setJobId] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const handleJobStarted = useCallback((id: string) => {
    setJobId(id);
    setDone(false);
  }, []);

  const handleComplete = useCallback(() => {
    setDone(true);
  }, []);

  return (
    <div className="min-h-screen bg-zinc-50">
      <header className="bg-white border-b border-zinc-200 px-6 py-4 flex items-center justify-between">
        <div>
          <Link href="/" className="font-bold text-zinc-900 text-lg tracking-tight hover:text-blue-600 transition-colors">
            Place Extractor
          </Link>
          <p className="text-xs text-zinc-400">Extract and map locations from social posts</p>
        </div>
        <Link
          href="/"
          className="text-sm text-zinc-500 hover:text-zinc-900 border border-zinc-200 rounded-lg px-3 py-1.5 transition-colors"
        >
          View dashboard
        </Link>
      </header>

      <main className="max-w-screen-sm mx-auto px-6 py-12 flex flex-col items-center gap-8">
        {!jobId && <UploadCard onJobStarted={handleJobStarted} />}

        {jobId && (
          <div className="w-full space-y-6">
            <JobStatus
              jobId={jobId}
              onComplete={handleComplete}
              onPlacesUpdate={() => {}}
            />
            {done && (
              <Link
                href="/"
                className="block text-center text-sm font-medium text-blue-600 hover:underline"
              >
                View extracted places on dashboard →
              </Link>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
