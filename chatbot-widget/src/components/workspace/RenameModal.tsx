/**
 * Fakhri ERP AI — Rename Conversation Modal
 * Provides title validation and immediate persistence update.
 */
import React, { useState, useEffect, useRef } from "react";
import { PencilIcon, XIcon } from "../ui/Icons";
import { IconButton } from "../ui/IconButton";
import { Button } from "../ui/Button";
import { validateTitle } from "../../services/sessionStore";

export interface RenameModalProps {
  isOpen: boolean;
  currentTitle: string;
  onClose: () => void;
  onSave: (newTitle: string) => void;
}

export const RenameModal: React.FC<RenameModalProps> = ({
  isOpen,
  currentTitle,
  onClose,
  onSave,
}) => {
  const [title, setTitle] = useState(currentTitle);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isOpen) {
      setTitle(currentTitle);
      setError(null);
      setTimeout(() => inputRef.current?.select(), 50);
    }
  }, [isOpen, currentTitle]);

  if (!isOpen) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const validation = validateTitle(title);
    if (!validation.valid) {
      setError(validation.error || "Invalid title");
      return;
    }
    onSave(title.trim());
    onClose();
  };

  return (
    <div className="fkh-modal-backdrop" onClick={onClose}>
      <div
        className="fkh-modal-panel fkh-rename-modal-panel"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="fkh-rename-title"
      >
        <div className="fkh-modal-header">
          <div className="fkh-modal-title-wrap">
            <PencilIcon size={16} />
            <h3 id="fkh-rename-title" className="fkh-modal-title">Rename Conversation</h3>
          </div>
          <IconButton
            icon={<XIcon size={16} />}
            title="Close dialog"
            aria-label="Close dialog"
            onClick={onClose}
            size="sm"
          />
        </div>

        <form onSubmit={handleSubmit} className="fkh-modal-body">
          <label htmlFor="rename-input" className="fkh-form-label">
            Conversation Title
          </label>
          <input
            id="rename-input"
            ref={inputRef}
            type="text"
            className={`fkh-form-input ${error ? "fkh-input-error" : ""}`}
            value={title}
            onChange={(e) => {
              setTitle(e.target.value);
              if (error) setError(null);
            }}
            placeholder="e.g. Organization hierarchy review"
            maxLength={80}
          />
          {error && <span className="fkh-form-error-text">{error}</span>}

          <div className="fkh-modal-footer">
            <Button variant="secondary" size="md" onClick={onClose} type="button">
              Cancel
            </Button>
            <Button variant="primary" size="md" type="submit">
              Save
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
};
