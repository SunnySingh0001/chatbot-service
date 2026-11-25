/**
 * Fakhri ERP AI — Delete Conversation Confirmation Modal
 * Prevents accidental thread deletions with explicit confirmation.
 */
import React from "react";
import { TrashIcon, XIcon } from "../ui/Icons";
import { IconButton } from "../ui/IconButton";
import { Button } from "../ui/Button";

export interface DeleteConfirmModalProps {
  isOpen: boolean;
  conversationTitle: string;
  onClose: () => void;
  onConfirm: () => void;
}

export const DeleteConfirmModal: React.FC<DeleteConfirmModalProps> = ({
  isOpen,
  conversationTitle,
  onClose,
  onConfirm,
}) => {
  if (!isOpen) return null;

  return (
    <div className="fkh-modal-backdrop" onClick={onClose}>
      <div
        className="fkh-modal-panel fkh-delete-modal-panel"
        onClick={(e) => e.stopPropagation()}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="fkh-delete-title"
        aria-describedby="fkh-delete-desc"
      >
        <div className="fkh-modal-header">
          <div className="fkh-modal-title-wrap fkh-danger-title-wrap">
            <TrashIcon size={16} />
            <h3 id="fkh-delete-title" className="fkh-modal-title">Delete Conversation</h3>
          </div>
          <IconButton
            icon={<XIcon size={16} />}
            title="Cancel"
            aria-label="Cancel"
            onClick={onClose}
            size="sm"
          />
        </div>

        <div className="fkh-modal-body">
          <p id="fkh-delete-desc" className="fkh-delete-confirm-text">
            Are you sure you want to delete <strong>"{conversationTitle}"</strong>?
          </p>
          <p className="fkh-delete-subtext">
            This conversation and its messages will be removed from your history. This action cannot be undone.
          </p>

          <div className="fkh-modal-footer">
            <Button variant="secondary" size="md" onClick={onClose} type="button">
              Cancel
            </Button>
            <Button
              variant="danger"
              size="md"
              type="button"
              onClick={() => {
                onConfirm();
                onClose();
              }}
            >
              Delete Conversation
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
};
