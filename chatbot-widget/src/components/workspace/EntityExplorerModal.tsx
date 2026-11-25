/**
 * Fakhri ERP AI — Dynamic Entity Intelligence & Relationship Explorer Modal
 * Displays entity overview, multi-hop relationship tree, mapping fields, paginated records, and CSV/JSON export.
 */
import React, { useState, useEffect, useRef } from "react";
import { DatabaseIcon, SearchIcon, XIcon, ShieldCheckIcon } from "../ui/Icons";
import { IconButton } from "../ui/IconButton";
import { Badge } from "../ui/Badge";
import {
  EntityDetailsPayload,
  EntityIdentityItem,
  RelationshipItem,
  searchEntities,
  getEntityDetails,
  getEntityRecordsBatch,
  downloadEntityExport,
} from "../../services/entityService";

export interface EntityExplorerModalProps {
  isOpen: boolean;
  apiUrl: string;
  authToken: string;
  activeRole: string;
  onClose: () => void;
  onAskInChat: (promptText: string) => void;
}

const QUICK_ENTITY_CHIPS = [
  { label: "Amazon (Marketplace)", query: "Amazon" },
  { label: "Nishu Sarathe (Staff)", query: "Nishu Sarathe" },
  { label: "TSK-812 (Task)", query: "TSK-812" },
  { label: "Sales Dept (DEP-SALES)", query: "DEP-SALES" },
];

export const EntityExplorerModal: React.FC<EntityExplorerModalProps> = ({
  isOpen,
  apiUrl,
  authToken,
  activeRole,
  onClose,
  onAskInChat,
}) => {
  const [query, setQuery] = useState("");
  const [entityTypeFilter, setEntityTypeFilter] = useState("");
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [searchHits, setSearchHits] = useState<EntityIdentityItem[]>([]);
  const [details, setDetails] = useState<EntityDetailsPayload | null>(null);
  const [expandedRel, setExpandedRel] = useState<string | null>(null);
  const [relFilterMap, setRelFilterMap] = useState<Record<string, string>>({});
  const [relLoadingMap, setRelLoadingMap] = useState<Record<string, boolean>>({});
  const [exporting, setExporting] = useState<"csv" | "json" | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isOpen) {
      setTimeout(() => inputRef.current?.focus(), 60);
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const isClientBlocked = activeRole === "client" || !authToken;

  const handleSearchOrResolve = async (targetQuery?: string, explicitType?: string) => {
    const q = (targetQuery ?? query).trim();
    if (!q || isClientBlocked) return;
    setLoading(true);
    setErrorMsg(null);
    setExpandedRel(null);
    try {
      const payload = await getEntityDetails(apiUrl, authToken, q, explicitType || entityTypeFilter || undefined);
      setDetails(payload);
      if (!payload.entity && !payload.ambiguous) {
        const list = await searchEntities(apiUrl, authToken, q, explicitType || entityTypeFilter || undefined);
        setSearchHits(list);
      } else {
        setSearchHits([]);
      }
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Failed to inspect entity.");
    } finally {
      setLoading(false);
    }
  };

  const handleLoadNextBatch = async (rel: RelationshipItem) => {
    if (!details?.entity) return;
    const key = rel.relation_name;
    setRelLoadingMap((prev) => ({ ...prev, [key]: true }));
    try {
      const filterText = relFilterMap[key] || "";
      const nextPage = await getEntityRecordsBatch(
        apiUrl,
        authToken,
        details.entity.id,
        key,
        {
          offset: rel.records.length,
          cursor: rel.next_cursor ?? undefined,
          batchSize: 25,
          q: filterText,
          entityType: details.entity.type,
        }
      );
      setDetails((prev) => {
        if (!prev) return prev;
        const updatedRels = prev.relationships.map((item) => {
          if (item.relation_name !== key) return item;
          const seen = new Set(item.records.map((r) => String(r._id ?? "")));
          const merged = [...item.records];
          for (const rec of nextPage.records) {
            const rid = String(rec._id ?? "");
            if (!rid || !seen.has(rid)) {
              if (rid) seen.add(rid);
              merged.push(rec);
            }
          }
          return {
            ...item,
            total_records: nextPage.total_records,
            retrieved_records: merged.length,
            has_more: nextPage.has_more,
            next_cursor: nextPage.next_cursor,
            records: merged,
          };
        });
        const totalAll = updatedRels.filter((r) => r.accessible).reduce((a, b) => a + b.total_records, 0);
        const loadedAll = updatedRels.filter((r) => r.accessible).reduce((a, b) => a + b.retrieved_records, 0);
        const partial = updatedRels.filter((r) => r.accessible && (r.has_more || r.retrieved_records < r.total_records));
        return {
          ...prev,
          relationships: updatedRels,
          retrieval: {
            ...prev.retrieval,
            complete: partial.length === 0,
            total_records: totalAll,
            retrieved_records: loadedAll,
            partial_relationships: partial.map((r) => `${r.relation_name} (${r.retrieved_records}/${r.total_records})`),
          },
        };
      });
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Failed to load batch.");
    } finally {
      setRelLoadingMap((prev) => ({ ...prev, [key]: false }));
    }
  };

  const handleFilterRelation = async (rel: RelationshipItem, text: string) => {
    setRelFilterMap((prev) => ({ ...prev, [rel.relation_name]: text }));
    if (!details?.entity) return;
    setRelLoadingMap((prev) => ({ ...prev, [rel.relation_name]: true }));
    try {
      const filteredPage = await getEntityRecordsBatch(
        apiUrl,
        authToken,
        details.entity.id,
        rel.relation_name,
        { offset: 0, batchSize: 25, q: text, entityType: details.entity.type }
      );
      setDetails((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          relationships: prev.relationships.map((item) =>
            item.relation_name === rel.relation_name
              ? {
                  ...item,
                  total_records: filteredPage.total_records,
                  retrieved_records: filteredPage.records.length,
                  has_more: filteredPage.has_more,
                  next_cursor: filteredPage.next_cursor,
                  records: filteredPage.records,
                }
              : item
          ),
        };
      });
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Failed to filter records.");
    } finally {
      setRelLoadingMap((prev) => ({ ...prev, [rel.relation_name]: false }));
    }
  };

  const handleExport = async (fmt: "csv" | "json") => {
    if (!details?.entity) return;
    setExporting(fmt);
    setErrorMsg(null);
    try {
      await downloadEntityExport(apiUrl, authToken, details.entity.id, fmt);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Export failed.");
    } finally {
      setExporting(null);
    }
  };

  const formatCell = (val: unknown): string => {
    if (val === null || val === undefined) return "—";
    if (typeof val === "object") return JSON.stringify(val).slice(0, 80);
    return String(val).slice(0, 80);
  };

  return (
    <div className="fkh-modal-backdrop" onClick={onClose}>
      <div
        className="fkh-modal-panel fkh-entity-modal-panel"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Entity Intelligence and Relationship Explorer"
      >
        {/* Header & Search */}
        <div className="fkh-entity-modal-header">
          <div className="fkh-entity-title-row">
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <DatabaseIcon size={18} />
              <h2 className="fkh-entity-modal-heading">Dynamic Entity Explorer</h2>
              <Badge variant="blue" size="sm">{activeRole.toUpperCase()}</Badge>
            </div>
            <IconButton icon={<XIcon size={16} />} title="Close Explorer" aria-label="Close Explorer" onClick={onClose} size="sm" />
          </div>

          <form
            className="fkh-entity-search-bar"
            onSubmit={(e) => {
              e.preventDefault();
              void handleSearchOrResolve();
            }}
          >
            <SearchIcon size={16} className="fkh-search-icon" />
            <input
              ref={inputRef}
              type="text"
              className="fkh-search-input-field"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search any entity name, ID, code (e.g. Amazon, Nishu Sarathe, TSK-812, FISIPL...)"
              aria-label="Search entity identifier"
            />
            <select
              className="fkh-entity-type-select"
              value={entityTypeFilter}
              onChange={(e) => setEntityTypeFilter(e.target.value)}
              aria-label="Filter entity type"
            >
              <option value="">All Types</option>
              <option value="Client">Client</option>
              <option value="Employee">Employee</option>
              <option value="AccountType">Marketplace</option>
              <option value="Department">Department</option>
              <option value="PricingPlan">Pricing Plan</option>
              <option value="Team">Team</option>
              <option value="Task">Task</option>
              <option value="Lead">Lead</option>
            </select>
            <button type="submit" className="fkh-entity-primary-btn" disabled={loading || isClientBlocked}>
              {loading ? "Discovering..." : "Explore"}
            </button>
          </form>

          <div className="fkh-entity-quick-chips">
            {QUICK_ENTITY_CHIPS.map((chip) => (
              <button
                key={chip.query}
                type="button"
                className="fkh-entity-chip"
                onClick={() => {
                  setQuery(chip.query);
                  void handleSearchOrResolve(chip.query);
                }}
              >
                {chip.label}
              </button>
            ))}
          </div>
        </div>

        {/* Body */}
        <div className="fkh-entity-modal-body">
          {isClientBlocked && (
            <div className="fkh-entity-notice-warn">
              <ShieldCheckIcon size={16} />
              <span>Entity Explorer requires an authenticated internal staff role. Client roles are isolated from cross-collection exploration.</span>
            </div>
          )}

          {errorMsg && <div className="fkh-entity-notice-err">{errorMsg}</div>}

          {/* Disambiguation Picker */}
          {details?.ambiguous && details.candidates.length > 0 && (
            <div className="fkh-entity-section-card">
              <h3 className="fkh-entity-section-title">
                Multiple entities matched ({details.candidates.length}) — select the exact record:
              </h3>
              <div className="fkh-entity-candidates-grid">
                {details.candidates.map((cand) => (
                  <button
                    key={`${cand.collection}-${cand.id}`}
                    type="button"
                    className="fkh-entity-candidate-btn"
                    onClick={() => {
                      setQuery(cand.name);
                      void handleSearchOrResolve(cand.id, cand.type);
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px" }}>
                      <strong>{cand.name}</strong>
                      <Badge variant="slate" size="sm">{cand.type}</Badge>
                    </div>
                    <span className="fkh-entity-meta-sub">
                      {cand.collection}{cand.code ? ` · ${cand.code}` : ""}{cand.subtitle ? ` · ${cand.subtitle}` : ""}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {!details?.entity && !details?.ambiguous && searchHits.length === 0 && !loading && !isClientBlocked && (
            <div className="fkh-entity-empty">
              Enter any entity name, ID, or code above to dynamically discover database mappings, multi-hop relationships, paginated records, and completeness status.
            </div>
          )}

          {/* Resolved Entity View */}
          {details?.entity && (
            <>
              {/* Entity Overview + Completeness + Exports */}
              <div className="fkh-entity-section-card">
                <div className="fkh-entity-overview-top">
                  <div>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                      <h3 style={{ margin: 0, fontSize: "16px", color: "var(--fkh-text-primary)" }}>{details.entity.name}</h3>
                      <Badge variant="blue" size="sm">{details.entity.type}</Badge>
                      <Badge variant={details.retrieval.complete ? "green" : "amber"} size="sm">
                        {details.retrieval.complete
                          ? `Complete (${details.retrieval.retrieved_records}/${details.retrieval.total_records})`
                          : `Partial (${details.retrieval.retrieved_records}/${details.retrieval.total_records})`}
                      </Badge>
                    </div>
                    <div className="fkh-entity-meta-sub" style={{ marginTop: "4px" }}>
                      ID: <code>{details.entity.id}</code> · Collection: <code>mongo:{details.entity.collection}</code>
                      {" · "}Mapped Locations: {details.database_locations.map((l) => `mongo:${l}`).join(", ")}
                    </div>
                  </div>
                  <div className="fkh-entity-export-actions">
                    <button
                      type="button"
                      className="fkh-entity-secondary-btn"
                      onClick={() => void handleExport("csv")}
                      disabled={exporting !== null}
                    >
                      {exporting === "csv" ? "Exporting..." : "Export CSV"}
                    </button>
                    <button
                      type="button"
                      className="fkh-entity-secondary-btn"
                      onClick={() => void handleExport("json")}
                      disabled={exporting !== null}
                    >
                      {exporting === "json" ? "Exporting..." : "Export JSON"}
                    </button>
                    <button
                      type="button"
                      className="fkh-entity-primary-btn"
                      onClick={() => {
                        onAskInChat(`Give me complete information about ${details.entity?.name} (ID: ${details.entity?.id}) and show everywhere it is mapped`);
                        onClose();
                      }}
                    >
                      Ask in Chat
                    </button>
                  </div>
                </div>

                {details.retrieval.warnings.length > 0 && (
                  <div className="fkh-entity-warnings">
                    {details.retrieval.warnings.map((w, idx) => (
                      <div key={idx}>{w}</div>
                    ))}
                  </div>
                )}

                {details.relationship_tree && (
                  <pre className="fkh-entity-tree-pre">{details.relationship_tree}</pre>
                )}
              </div>

              {/* Connected Relationships & Paginated Records */}
              <div className="fkh-entity-rels-list">
                {details.relationships.map((rel) => {
                  const isExp = expandedRel === rel.relation_name;
                  const sampleCols = rel.records.length > 0
                    ? Object.keys(rel.records[0]).filter((k) => !["_id", "__v", "updates", "messages", "evidence"].includes(k)).slice(0, 6)
                    : [];
                  return (
                    <div key={`${rel.hop}-${rel.relation_name}`} className="fkh-entity-rel-card">
                      <div
                        className="fkh-entity-rel-header"
                        onClick={() => rel.accessible && setExpandedRel(isExp ? null : rel.relation_name)}
                        role="button"
                        tabIndex={0}
                        onKeyDown={(e) => {
                          if ((e.key === "Enter" || e.key === " ") && rel.accessible) {
                            e.preventDefault();
                            setExpandedRel(isExp ? null : rel.relation_name);
                          }
                        }}
                      >
                        <div>
                          <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                            <strong>{rel.target}</strong>
                            <Badge variant={rel.hop === 1 ? "blue" : "purple"} size="sm">
                              {rel.hop === 1 ? "Direct" : `Indirect Hop ${rel.hop}`}
                            </Badge>
                            <span className="fkh-entity-meta-sub">{rel.relationship_type}</span>
                          </div>
                          <div className="fkh-entity-meta-sub" style={{ marginTop: "2px" }}>
                            Path: <code>{rel.path.join(" -> ")}</code> · Mapping: <code>{rel.mapping_field}</code>
                          </div>
                        </div>
                        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                          {rel.accessible ? (
                            <Badge variant={rel.has_more ? "amber" : "green"} size="sm">
                              {rel.retrieved_records} / {rel.total_records} records
                            </Badge>
                          ) : (
                            <Badge variant="red" size="sm">Restricted ({rel.restriction_reason})</Badge>
                          )}
                          {rel.accessible && (
                            <span style={{ fontSize: "12px", color: "var(--fkh-text-secondary)" }}>
                              {isExp ? "▲" : "▼"}
                            </span>
                          )}
                        </div>
                      </div>

                      {isExp && rel.accessible && (
                        <div className="fkh-entity-rel-body">
                          <div className="fkh-entity-rel-toolbar">
                            <input
                              type="text"
                              className="fkh-entity-rel-filter"
                              value={relFilterMap[rel.relation_name] || ""}
                              onChange={(e) => void handleFilterRelation(rel, e.target.value)}
                              placeholder={`Filter ${rel.target} records...`}
                              aria-label={`Filter ${rel.target} records`}
                            />
                            {rel.has_more && (
                              <button
                                type="button"
                                className="fkh-entity-secondary-btn"
                                onClick={() => void handleLoadNextBatch(rel)}
                                disabled={Boolean(relLoadingMap[rel.relation_name])}
                              >
                                {relLoadingMap[rel.relation_name]
                                  ? "Loading..."
                                  : `Load Next Batch (${Math.min(25, rel.total_records - rel.retrieved_records)} more)`}
                              </button>
                            )}
                          </div>

                          {rel.records.length === 0 ? (
                            <div className="fkh-entity-meta-sub">No matching records in this slice.</div>
                          ) : (
                            <div className="fkh-entity-table-scroll">
                              <table className="fkh-entity-table">
                                <thead>
                                  <tr>
                                    <th>ID</th>
                                    {sampleCols.map((col) => <th key={col}>{col}</th>)}
                                  </tr>
                                </thead>
                                <tbody>
                                  {rel.records.map((rec, rIdx) => (
                                    <tr key={String(rec._id ?? rIdx)}>
                                      <td><code>{String(rec._id ?? "").slice(-8)}</code></td>
                                      {sampleCols.map((col) => <td key={col}>{formatCell(rec[col])}</td>)}
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
};
