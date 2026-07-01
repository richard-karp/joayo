'use client';

import { useCallback, useState } from "react";
import { useDropzone } from "react-dropzone";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { submitExtract } from "@/lib/api";

const BASE = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

interface Collection {
  name: string;
  count: number;
}

interface Props {
  onJobStarted: (jobId: string) => void;
}

export default function UploadCard({ onJobStarted }: Props) {
  const [mode, setMode] = useState<"file" | "text">("file");
  const [urlText, setUrlText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [collections, setCollections] = useState<Collection[] | null>(null);
  const [selectedCollection, setSelectedCollection] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onDrop = useCallback(async (accepted: File[]) => {
    const f = accepted[0];
    if (!f) return;
    setFile(f);
    setCollections(null);
    setSelectedCollection("");
    setError(null);

    // Check if this is a collections file
    const formData = new FormData();
    formData.append("file", f);
    try {
      const res = await fetch(`${BASE}/api/collections`, { method: "POST", body: formData });
      if (res.ok) {
        const cols: Collection[] = await res.json();
        if (cols.length > 0) {
          const withPosts = cols.filter((c) => c.count > 0);
          setCollections(withPosts.length > 0 ? withPosts : cols);
          setSelectedCollection(withPosts.length > 0 ? withPosts[0].name : cols[0].name);
        }
      }
    } catch {
      // Not a collections file or network error — proceed as saved_posts.json
    }
  }, []);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { "application/json": [".json"] },
    maxFiles: 1,
  });

  async function handleSubmit() {
    setError(null);
    const formData = new FormData();
    if (mode === "file" && file) {
      formData.append("file", file);
      if (collections && selectedCollection) {
        formData.append("collection", selectedCollection);
      }
    } else if (mode === "text" && urlText.trim()) {
      formData.append("urls", urlText.trim());
    } else {
      setError(mode === "file" ? "Please drop a JSON file." : "Please paste at least one URL.");
      return;
    }

    setLoading(true);
    try {
      const { job_id } = await submitExtract(formData);
      onJobStarted(job_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Submission failed.");
    } finally {
      setLoading(false);
    }
  }

  const selectedCol = collections?.find((c) => c.name === selectedCollection);

  return (
    <Card className="w-full max-w-lg">
      <CardHeader>
        <CardTitle>Extract Locations</CardTitle>
        <CardDescription>
          Upload an Instagram saved_posts.json or saved_collections.json, or paste post URLs.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Mode tabs */}
        <div className="flex rounded-lg border overflow-hidden text-sm font-medium">
          <button
            className={`flex-1 py-2 transition-colors ${mode === "file" ? "bg-zinc-900 text-white" : "bg-white text-zinc-600 hover:bg-zinc-50"}`}
            onClick={() => setMode("file")}
          >
            JSON File
          </button>
          <button
            className={`flex-1 py-2 transition-colors ${mode === "text" ? "bg-zinc-900 text-white" : "bg-white text-zinc-600 hover:bg-zinc-50"}`}
            onClick={() => setMode("text")}
          >
            Paste URLs
          </button>
        </div>

        {mode === "file" ? (
          <>
            <div
              {...getRootProps()}
              className={`border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors ${
                isDragActive ? "border-blue-500 bg-blue-50" : "border-zinc-300 hover:border-zinc-400"
              }`}
            >
              <input {...getInputProps()} />
              {file ? (
                <p className="text-sm text-zinc-700">
                  <span className="font-medium">{file.name}</span>{" "}
                  <span className="text-zinc-400">({(file.size / 1024).toFixed(1)} KB)</span>
                </p>
              ) : isDragActive ? (
                <p className="text-sm text-blue-600">Drop the file here…</p>
              ) : (
                <p className="text-sm text-zinc-500">
                  Drop <span className="font-medium">saved_posts.json</span> or{" "}
                  <span className="font-medium">saved_collections.json</span> here, or click to browse
                </p>
              )}
            </div>

            {collections && collections.length > 0 && (
              <div className="space-y-1">
                <label className="text-sm font-medium text-zinc-700">Collection</label>
                <select
                  className="w-full rounded-lg border border-zinc-300 px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-zinc-900"
                  value={selectedCollection}
                  onChange={(e) => setSelectedCollection(e.target.value)}
                >
                  {collections.map((c) => (
                    <option key={c.name} value={c.name}>
                      {c.name} ({c.count} posts)
                    </option>
                  ))}
                </select>
                {selectedCol && (
                  <p className="text-xs text-zinc-400">{selectedCol.count} posts will be processed</p>
                )}
              </div>
            )}
          </>
        ) : (
          <textarea
            className="w-full h-32 rounded-lg border border-zinc-300 px-3 py-2 text-sm font-mono resize-none focus:outline-none focus:ring-2 focus:ring-zinc-900"
            placeholder={"https://www.instagram.com/p/ABC123/\nhttps://www.instagram.com/reel/XYZ789/"}
            value={urlText}
            onChange={(e) => setUrlText(e.target.value)}
          />
        )}

        {error && <p className="text-sm text-red-600">{error}</p>}

        <Button
          className="w-full"
          onClick={handleSubmit}
          disabled={loading}
        >
          {loading ? "Submitting…" : "Extract Places"}
        </Button>
      </CardContent>
    </Card>
  );
}
