"use client";

import { makeAssistantToolUI } from "@assistant-ui/react";
import type { Source } from "../lib/chatStream";
import { Citations } from "./Citations";

export const SourcesToolUI = makeAssistantToolUI<{ sources: Source[] }, unknown>({
  toolName: "praxia_sources",
  render: ({ args }) => <Citations sources={args.sources} />,
});
