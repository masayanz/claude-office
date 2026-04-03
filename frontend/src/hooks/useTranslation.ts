"use client";

import { useMemo } from "react";
import { usePreferencesStore } from "@/stores/preferencesStore";
import { getTranslation, type TranslationKey } from "@/i18n";

export function useTranslation() {
  const language = usePreferencesStore((s) => s.language);
  const t = useMemo(() => getTranslation(language), [language]);
  return { t, language };
}

export type { TranslationKey };
