"use client";

import { useState } from "react";

function fmt(v: unknown): string {
  return v == null ? "" : String(v);
}

export function SqlTable({
  columns,
  rows,
  sql,
}: {
  columns: string[];
  rows: Record<string, unknown>[];
  sql?: string;
}) {
  const [showSql, setShowSql] = useState(false);
  if (!rows.length) return <p className="my-2 text-sm text-gray-500">Sin resultados.</p>;
  const cols = columns.length ? columns : Object.keys(rows[0]);
  return (
    <div className="my-2">
      <div className="max-h-80 overflow-auto rounded-md border border-gray-200">
        <table className="w-full border-collapse text-sm">
          <thead className="sticky top-0 bg-gray-50">
            <tr>
              {cols.map((c) => (
                <th
                  key={c}
                  className="border-b border-gray-200 px-3 py-1.5 text-left font-semibold text-gray-700"
                >
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className={i % 2 ? "bg-gray-50/60" : undefined}>
                {cols.map((c) => (
                  <td key={c} className="border-b border-gray-100 px-3 py-1.5 text-gray-800">
                    {fmt(r[c])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {sql ? (
        <div className="mt-1 text-xs">
          <button className="text-gray-500 underline" onClick={() => setShowSql((v) => !v)}>
            {showSql ? "ocultar consulta" : "ver consulta"}
          </button>
          {showSql ? (
            <pre className="mt-1 overflow-auto rounded bg-gray-100 p-2 text-gray-700">{sql}</pre>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
