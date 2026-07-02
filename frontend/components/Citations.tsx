"use client";

import type { Source } from "../lib/chatStream";

export function Citations({ sources }: { sources: Source[] }) {
  if (!sources.length) return null;
  return (
    <div className="mt-2 border-t border-gray-200 pt-2 text-sm text-gray-600">
      <p className="mb-1 font-semibold text-gray-700">Fuentes</p>
      <ol className="space-y-0.5">
        {sources.map((s) => (
          <li key={s.n} className="flex gap-1.5">
            <span className="font-mono text-gray-400">[{s.n}]</span>
            <span>
              {s.title}
              {s.page != null ? ` — p.${s.page}` : ""}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
