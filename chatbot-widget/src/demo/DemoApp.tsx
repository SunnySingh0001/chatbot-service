/**
 * Demo host page providing in-memory token switching and mounting ChatWidget.
 */
import React, { useState } from "react";
import { ChatWidget } from "../components/ChatWidget";
import "../styles/widget.css";
import "./demo.css";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

export const DemoApp: React.FC = () => {
  const [pastedInput, setPastedInput] = useState("");
  const [employeeToken, setEmployeeToken] = useState("");
  const [hrToken, setHrToken] = useState("");
  const [activeRole, setActiveRole] = useState<"employee" | "hr" | "none">("none");

  // In-memory token lookup function for the widget
  const getActiveToken = (): string => {
    if (activeRole === "employee") {
      return employeeToken;
    }
    if (activeRole === "hr") {
      return hrToken;
    }
    return "";
  };

  const handleSaveEmployee = () => {
    if (pastedInput.trim()) {
      setEmployeeToken(pastedInput.trim());
      setActiveRole("employee");
      setPastedInput("");
    }
  };

  const handleSaveHr = () => {
    if (pastedInput.trim()) {
      setHrToken(pastedInput.trim());
      setActiveRole("hr");
      setPastedInput("");
    }
  };

  const activeToken = getActiveToken();

  return (
    <div className="demo-container">
      <header className="demo-header">
        <h1>Internal Employee Chatbot — Demo Host</h1>
        <p className="demo-subtitle">
          Testing ground for the embedded React Chat Widget with runtime role switching.
        </p>
      </header>

      <main className="demo-main">
        <section className="demo-card">
          <h2>Authentication Setup (In-Memory Only)</h2>
          <p className="demo-desc">
            Generate development JWTs using: <code>python -m scripts.generate_tokens</code>.
            Paste the token below and assign it to a role. Tokens are kept strictly in memory.
          </p>

          <div className="demo-input-group">
            <textarea
              className="demo-token-input"
              value={pastedInput}
              onChange={(e) => setPastedInput(e.target.value)}
              placeholder="Paste raw JWT token here..."
              rows={3}
            />
            <div className="demo-save-actions">
              <button
                type="button"
                className="demo-btn demo-btn-secondary"
                onClick={handleSaveEmployee}
                disabled={!pastedInput.trim()}
              >
                Assign to Employee Slot
              </button>
              <button
                type="button"
                className="demo-btn demo-btn-secondary"
                onClick={handleSaveHr}
                disabled={!pastedInput.trim()}
              >
                Assign to HR Slot
              </button>
            </div>
          </div>

          <div className="demo-switcher-group">
            <span className="demo-switcher-label">Active Role Switcher:</span>
            <div className="demo-switcher-buttons">
              <button
                type="button"
                className={`demo-role-btn ${activeRole === "employee" ? "active" : ""}`}
                onClick={() => setActiveRole("employee")}
                disabled={!employeeToken}
              >
                Use employee token {employeeToken ? "✓" : "(empty)"}
              </button>
              <button
                type="button"
                className={`demo-role-btn ${activeRole === "hr" ? "active" : ""}`}
                onClick={() => setActiveRole("hr")}
                disabled={!hrToken}
              >
                Use hr token {hrToken ? "✓" : "(empty)"}
              </button>
            </div>
          </div>

          <div className="demo-status-pill">
            <span>Status:</span>
            <strong>
              {activeToken
                ? `Using ${activeRole.toUpperCase()} Token (${activeToken.slice(0, 10)}...)`
                : "No token active (Chat calls will prompt for authentication)"}
            </strong>
          </div>
        </section>

        <section className="demo-card">
          <h2>Backend Connection Details</h2>
          <ul className="demo-info-list">
            <li>
              <strong>Target API:</strong> <code>{API_URL}</code>
            </li>
            <li>
              <strong>Streaming:</strong> Server-Sent Events (SSE) via POST <code>/chat</code>
            </li>
            <li>
              <strong>Access Levels:</strong> Employee accesses general documents; HR accesses
              general + HR confidential compensation files.
            </li>
          </ul>
        </section>
      </main>

      {/* Embedded Chat Widget mounted with runtime token getter */}
      <ChatWidget apiUrl={API_URL} getToken={getActiveToken} />
    </div>
  );
};
