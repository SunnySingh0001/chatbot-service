/**
 * Scroll position and auto-follow helpers for the chat message list.
 */

export interface ScrollMetrics {
  scrollHeight: number;
  scrollTop: number;
  clientHeight: number;
}

export const JUMP_SHOW_THRESHOLD_PX = 120;
export const BOTTOM_SNAP_TOLERANCE_PX = 36;
export const MIN_OVERFLOW_PX = 24;

/** Computes pixels remaining between the viewport bottom and total scrollable height. */
export function getDistanceFromBottom(metrics: ScrollMetrics): number {
  return Math.max(0, metrics.scrollHeight - metrics.scrollTop - metrics.clientHeight);
}

/** Returns true when content overflows and user has scrolled above the bottom threshold. */
export function shouldShowJumpToLatest(
  metrics: ScrollMetrics,
  thresholdPx: number = JUMP_SHOW_THRESHOLD_PX
): boolean {
  const hasOverflow = metrics.scrollHeight > metrics.clientHeight + MIN_OVERFLOW_PX;
  if (!hasOverflow) return false;
  return getDistanceFromBottom(metrics) > thresholdPx;
}

/** Returns true when the scroll position is effectively anchored at the bottom edge. */
export function isAtScrollBottom(
  metrics: ScrollMetrics,
  tolerancePx: number = BOTTOM_SNAP_TOLERANCE_PX
): boolean {
  return getDistanceFromBottom(metrics) <= tolerancePx;
}
