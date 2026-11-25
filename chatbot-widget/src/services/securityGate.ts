/**
 * Production security and feature-gating helpers for developer JWT tools and persistence-backed UI controls.
 */

export function resolveDevAuthEnabled(isDevEnv: boolean, optInFlag?: string): boolean {
  return Boolean(isDevEnv || optInFlag === "true");
}

export function canOpenAdminWorkspaceSettings(enableDevAuth: boolean, activeRole: string): boolean {
  return enableDevAuth || activeRole === "super-admin" || activeRole === "founder";
}

export function shouldRenderFeedbackControls(onPersistFeedback?: unknown): boolean {
  return typeof onPersistFeedback === "function";
}
