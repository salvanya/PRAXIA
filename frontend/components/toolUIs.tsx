"use client";

import { makeAssistantToolUI } from "@assistant-ui/react";
import type { Source, ProposedAction } from "../lib/chatStream";
import { Citations } from "./Citations";
import { SqlTable } from "./SqlTable";
import { ConfirmCard } from "./ConfirmCard";

export const SourcesToolUI = makeAssistantToolUI<{ sources: Source[] }, unknown>({
  toolName: "praxia_sources",
  render: ({ args }) => <Citations sources={args.sources} />,
});

export const SqlTableToolUI = makeAssistantToolUI<
  { columns: string[]; rows: Record<string, unknown>[]; sql?: string },
  unknown
>({
  toolName: "praxia_sql_table",
  render: ({ args }) => <SqlTable columns={args.columns} rows={args.rows} sql={args.sql} />,
});

export const ConfirmToolUI = makeAssistantToolUI<{ threadId: string; action: ProposedAction }, unknown>({
  toolName: "praxia_confirm",
  render: ({ args }) => <ConfirmCard threadId={args.threadId} action={args.action} />,
});
