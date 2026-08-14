/**
 * Shared bubble text utilities for speech/thought bubbles.
 *
 * Truncates text that exceeds the max character limit, appending "..."
 * to indicate truncation. Used by both AgentSprite and BossSprite bubbles.
 */

/** Maximum characters shown in a bubble before truncation. */
const BUBBLE_MAX_WIDTH = 56;

/** Approximate display width so Japanese glyphs do not collapse the bubble. */
export function bubbleTextWidth(text: string): number {
  return Array.from(text).reduce((width, character) => {
    return width + (/\p{Script=Han}|\p{Script=Hiragana}|\p{Script=Katakana}|\p{Extended_Pictographic}/u.test(character) ? 2 : 1);
  }, 0);
}

/**
 * Truncate bubble text to a maximum character length.
 * Text at or below the limit is returned unchanged.
 *
 * @param text - The bubble text to potentially truncate.
 * @param maxLen - Maximum character count (default 60).
 * @returns Truncated text with "..." suffix if over limit, otherwise original.
 */
export function truncateBubbleText(
  text: string,
  maxLen: number = BUBBLE_MAX_WIDTH,
): string {
  if (bubbleTextWidth(text) <= maxLen) return text;
  const suffix = "...";
  const suffixWidth = bubbleTextWidth(suffix);
  let result = "";
  for (const character of Array.from(text)) {
    if (bubbleTextWidth(result + character) + suffixWidth > maxLen) break;
    result += character;
  }
  return result + suffix;
}
