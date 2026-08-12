"use client";

/**
 * WeatherMode - Mode 5: Session health as a weather forecast.
 *
 * Maps the current success rate and error count to a weather condition
 * (Sunny / Cloudy / Rainy / Stormy) with stats displayed alongside.
 */

import { type ReactNode } from "react";
import type { WhiteboardData } from "@/types";
import { useTranslation } from "@/hooks/useTranslation";

export interface WeatherModeProps {
  data: WhiteboardData;
}

interface WeatherCondition {
  icon: string;
  labelKey:
    | "whiteboard.weather.stormy"
    | "whiteboard.weather.rainy"
    | "whiteboard.weather.cloudy"
    | "whiteboard.weather.sunny";
  color: string;
}

function getWeatherCondition(data: WhiteboardData): WeatherCondition {
  const recentSuccessCount = data.recentSuccessCount ?? 0;
  const recentErrorCount = data.recentErrorCount ?? 0;
  const activityLevel = data.activityLevel ?? 0;
  const totalOps = recentSuccessCount + recentErrorCount;
  const successRate = totalOps > 0 ? recentSuccessCount / totalOps : 1;

  if (recentErrorCount > 5) {
    return { icon: "⛈️", labelKey: "whiteboard.weather.stormy", color: "#7c3aed" };
  }
  if (successRate < 0.7) {
    return { icon: "🌧️", labelKey: "whiteboard.weather.rainy", color: "#3b82f6" };
  }
  if (activityLevel < 0.3) {
    return { icon: "⛅", labelKey: "whiteboard.weather.cloudy", color: "#6b7280" };
  }
  return { icon: "☀️", labelKey: "whiteboard.weather.sunny", color: "#f59e0b" };
}

export function WeatherMode({ data }: WeatherModeProps): ReactNode {
  const { t } = useTranslation();
  const recentSuccessCount = data.recentSuccessCount ?? 0;
  const recentErrorCount = data.recentErrorCount ?? 0;
  const totalOps = recentSuccessCount + recentErrorCount;
  const successRate = totalOps > 0 ? recentSuccessCount / totalOps : 1;
  const weather = getWeatherCondition(data);

  return (
    <pixiContainer>
      {/* Large weather icon */}
      <pixiText
        text={weather.icon}
        x={80}
        y={50}
        anchor={0.5}
        style={{ fontSize: 50 }}
        resolution={2}
      />

      {/* Weather label */}
      <pixiText
        text={t(weather.labelKey)}
        x={80}
        y={95}
        anchor={0.5}
        style={{
          fontFamily: '"Courier New", monospace',
          fontSize: 14,
          fontWeight: "bold",
          fill: weather.color,
        }}
        resolution={2}
      />

      {/* Stats */}
      <pixiContainer x={170} y={10}>
        <pixiText
          text={`${t("whiteboard.success")}: ${(successRate * 100).toFixed(0)}%`}
          style={{
            fontFamily: '"Courier New", monospace',
            fontSize: 10,
            fill: "#22c55e",
          }}
          resolution={2}
        />
        <pixiText
          text={`${t("whiteboard.errors")}: ${recentErrorCount}`}
          y={16}
          style={{
            fontFamily: '"Courier New", monospace',
            fontSize: 10,
            fill: "#ef4444",
          }}
          resolution={2}
        />
        <pixiText
          text={`${t("whiteboard.activity")}: ${((data.activityLevel ?? 0) * 100).toFixed(0)}%`}
          y={32}
          style={{
            fontFamily: '"Courier New", monospace',
            fontSize: 10,
            fill: "#3b82f6",
          }}
          resolution={2}
        />
        <pixiText
          text={`${t("whiteboard.totalOps")}: ${totalOps}`}
          y={48}
          style={{
            fontFamily: '"Courier New", monospace',
            fontSize: 10,
            fill: "#6b7280",
          }}
          resolution={2}
        />
      </pixiContainer>
    </pixiContainer>
  );
}
