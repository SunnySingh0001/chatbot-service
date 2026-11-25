/**
 * Pure focus and global key-redirection guards for the chat prompt composer.
 */

export interface KeyRedirectContext {
  key: string;
  ctrlKey: boolean;
  metaKey: boolean;
  altKey: boolean;
  isComposing?: boolean;
  isDisabled: boolean;
  isComposerFocused: boolean;
  activeTagName?: string | null;
  isActiveContentEditable?: boolean;
  hasOpenDialog: boolean;
}

const NON_REDIRECT_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT"]);

/** Returns true when a global printable keystroke should redirect focus to the chat composer. */
export function shouldRedirectKeyToComposer(ctx: KeyRedirectContext): boolean {
  if (ctx.isDisabled || ctx.isComposerFocused || ctx.hasOpenDialog) {
    return false;
  }
  if (ctx.ctrlKey || ctx.metaKey || ctx.altKey || Boolean(ctx.isComposing)) {
    return false;
  }
  if (ctx.key.length !== 1 || /^\s$/.test(ctx.key)) {
    return false;
  }
  if (ctx.isActiveContentEditable) {
    return false;
  }
  const tag = ctx.activeTagName ? ctx.activeTagName.toUpperCase() : "";
  return !NON_REDIRECT_TAGS.has(tag);
}

/** Returns true when the composer should auto-focus without triggering mobile keyboards. */
export function shouldAutoFocusComposer(
  viewportWidth: number,
  hasOpenDialog: boolean,
  isDisabled: boolean
): boolean {
  return viewportWidth > 768 && !hasOpenDialog && !isDisabled;
}

/** Checks whether a blocking workspace modal dialog is currently mounted in the DOM. */
export function hasActiveWorkspaceModal(doc: Document): boolean {
  return Boolean(
    doc.querySelector(
      ".fkh-modal-backdrop, .fkh-modal-panel[role='dialog'], [role='dialog'][aria-modal='true']:not(.chat-panel)"
    )
  );
}
