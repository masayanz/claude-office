"use client";

import { useMemo } from "react";
import { type Locale } from "date-fns";
import { ptBR as dateFnsPtBR, es as dateFnsEs } from "date-fns/locale";
import { usePreferencesStore } from "@/stores/preferencesStore";
import {
  getTranslation,
  type TranslationKey,
  type Locale as AppLocale,
} from "@/i18n";

const dateFnsLocaleMap: Partial<Record<AppLocale, Locale>> = {
  "pt-BR": dateFnsPtBR,
  es: dateFnsEs,
};

export function useTranslation() {
  const language = usePreferencesStore((s) => s.language);
  const t = useMemo(() => getTranslation(language), [language]);
  const dateFnsLocale = dateFnsLocaleMap[language];
  return { t, language, dateFnsLocale };
}

export type { TranslationKey };
