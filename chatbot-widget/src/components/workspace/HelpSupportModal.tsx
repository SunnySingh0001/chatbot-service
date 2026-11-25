/**
 * Fakhri ERP AI — Enterprise Help & Support Modal
 * Provides verified shortcuts, departmental routing, and assistance documentation.
 */
import React from "react";
import { XIcon, HelpCircleIcon } from "../ui/Icons";
import { IconButton } from "../ui/IconButton";
import { Button } from "../ui/Button";

export interface HelpSupportModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export const HelpSupportModal: React.FC<HelpSupportModalProps> = ({ isOpen, onClose }) => {
  if (!isOpen) return null;

  return (
    <div className="fkh-modal-backdrop" onClick={onClose}>
      <div
        className="fkh-modal-panel"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="fkh-help-title"
      >
        {/* Modal Header */}
        <div className="fkh-modal-header">
          <div className="fkh-modal-title-wrap">
            <HelpCircleIcon size={20} />
            <h3 id="fkh-help-title" className="fkh-modal-title">Help & Enterprise Assistance</h3>
          </div>
          <IconButton
            icon={<XIcon size={18} />}
            title="Close Help"
            aria-label="Close Help"
            onClick={onClose}
            size="sm"
          />
        </div>

        {/* Modal Body */}
        <div className="fkh-modal-body">
          {/* Section 1: Keyboard Shortcuts */}
          <div className="fkh-modal-section">
            <h4 className="fkh-modal-section-title">Keyboard Shortcuts</h4>
            <div className="fkh-shortcuts-grid">
              <div className="fkh-shortcut-row">
                <span className="fkh-shortcut-action">New Conversation</span>
                <span className="fkh-kbd-shortcut">⌘K / Ctrl+K</span>
              </div>
              <div className="fkh-shortcut-row">
                <span className="fkh-shortcut-action">Send Message</span>
                <span className="fkh-kbd-shortcut">Enter</span>
              </div>
              <div className="fkh-shortcut-row">
                <span className="fkh-shortcut-action">Newline in Prompt</span>
                <span className="fkh-kbd-shortcut">Shift + Enter</span>
              </div>
              <div className="fkh-shortcut-row">
                <span className="fkh-shortcut-action">Close Panels & Drawers</span>
                <span className="fkh-kbd-shortcut">Esc</span>
              </div>
            </div>
          </div>

          {/* Section 2: Department Inquiries */}
          <div className="fkh-modal-section">
            <h4 className="fkh-modal-section-title">Department Inquiries</h4>
            <p className="fkh-modal-text">
              Fakhri AI provides automated answers grounded in our MongoDB Atlas database. If you require manual escalation:
            </p>
            <ul className="fkh-department-list">
              <li>
                <strong>HR & Benefits:</strong> Disha Damwani (Head of HR)
              </li>
              <li>
                <strong>Operations & Approvals:</strong> Zainab Husain (Head of Operations)
              </li>
              <li>
                <strong>IT Support & Security:</strong> Ali Asgar (IT Infrastructure)
              </li>
              <li>
                <strong>Executive Leadership:</strong> Abdul Quadir (Founder) & Atul Bangre (Super Admin)
              </li>
            </ul>
          </div>

          {/* Section 3: Data Safety Invariant */}
          <div className="fkh-modal-note">
            <strong>Data Privacy Note:</strong> All answers are strictly governed by your authenticated JWT role. Sensitive documents (salaries, billing, admin settings) are never revealed to unauthorized roles.
          </div>
        </div>

        {/* Modal Footer */}
        <div className="fkh-modal-footer">
          <Button variant="secondary" size="md" onClick={onClose}>
            Close
          </Button>
        </div>
      </div>
    </div>
  );
};
