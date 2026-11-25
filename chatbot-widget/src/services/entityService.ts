/**
 * Client service for RBAC-enforced Dynamic Entity Explorer, schema, pagination, and CSV/JSON export.
 */

export interface EntityIdentityItem {
  id: string;
  name: string;
  type: string;
  collection: string;
  code?: string | null;
  subtitle?: string | null;
  matched_on?: string | null;
}

export interface RelationshipItem {
  relation_name: string;
  source: string;
  source_field: string;
  target: string;
  target_field: string;
  relationship_type: string;
  hop: number;
  path: string[];
  mapping_field: string;
  total_records: number;
  retrieved_records: number;
  has_more: boolean;
  next_cursor?: string | null;
  accessible: boolean;
  restriction_reason?: string | null;
  records: Array<Record<string, unknown>>;
}

export interface RetrievalCompletenessInfo {
  complete: boolean;
  total_records: number;
  retrieved_records: number;
  total_relationships: number;
  complete_relationships: number;
  partial_relationships: string[];
  inaccessible_relationships: string[];
  warnings: string[];
}

export interface EntityDetailsPayload {
  entity: EntityIdentityItem | null;
  ambiguous: boolean;
  candidates: EntityIdentityItem[];
  overview: Record<string, unknown>;
  attributes: Record<string, unknown>;
  database_locations: string[];
  relationship_tree: string;
  relationships: RelationshipItem[];
  inaccessible_relationships: string[];
  retrieval: RetrievalCompletenessInfo;
}

export interface BatchPagePayload {
  entity_id: string;
  entity_type: string;
  relation_name: string;
  source: string;
  target: string;
  total_records: number;
  retrieved_records: number;
  offset: number;
  batch_size: number;
  has_more: boolean;
  next_cursor?: string | null;
  batch_label: string;
  complete: boolean;
  records: Array<Record<string, unknown>>;
}

/**
 * Builds an authenticated query URL against backend /entities endpoints.
 */
export function buildEntityEndpointUrl(
  apiUrl: string,
  path: string,
  params: Record<string, string | number | boolean | undefined | null>
): string {
  const base = apiUrl.replace(/\/$/, "");
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && String(v).trim() !== "") {
      sp.set(k, String(v));
    }
  }
  return `${base}/entities${path}?${sp.toString()}`;
}

async function fetchEntityJson<T>(url: string, authToken: string): Promise<T> {
  const resp = await fetch(url, {
    method: "GET",
    headers: authToken ? { Authorization: `Bearer ${authToken}` } : {},
  });
  if (!resp.ok) {
    let msg = `Request failed (${resp.status})`;
    try {
      const errData = (await resp.json()) as { detail?: string };
      if (errData.detail) msg = errData.detail;
    } catch {
      // keep generic error status
    }
    throw new Error(msg);
  }
  return (await resp.json()) as T;
}

/** Searches entities by name, company, code, email, phone, or ID. */
export async function searchEntities(
  apiUrl: string,
  authToken: string,
  query: string,
  entityType?: string
): Promise<EntityIdentityItem[]> {
  const url = buildEntityEndpointUrl(apiUrl, "/search", { q: query, entity_type: entityType, limit: 20 });
  return fetchEntityJson<EntityIdentityItem[]>(url, authToken);
}

/** Retrieves full 360-degree entity details, multi-hop graph, counts, and completeness status. */
export async function getEntityDetails(
  apiUrl: string,
  authToken: string,
  identifier: string,
  entityType?: string
): Promise<EntityDetailsPayload> {
  const url = buildEntityEndpointUrl(apiUrl, "/details", {
    identifier,
    entity_type: entityType,
    max_depth: 2,
    batch_size: 20,
  });
  return fetchEntityJson<EntityDetailsPayload>(url, authToken);
}

/** Loads a specific paginated batch for an entity relationship with optional filter text. */
export async function getEntityRecordsBatch(
  apiUrl: string,
  authToken: string,
  identifier: string,
  relationName: string,
  options: { offset?: number; cursor?: string | null; batchSize?: number; q?: string; entityType?: string } = {}
): Promise<BatchPagePayload> {
  const url = buildEntityEndpointUrl(apiUrl, "/records", {
    identifier,
    relation_name: relationName,
    entity_type: options.entityType,
    offset: options.offset ?? 0,
    cursor: options.cursor ?? undefined,
    batch_size: options.batchSize ?? 25,
    q: options.q,
  });
  return fetchEntityJson<BatchPagePayload>(url, authToken);
}

/** Downloads complete accessible entity records as CSV or JSON via Blob trigger. */
export async function downloadEntityExport(
  apiUrl: string,
  authToken: string,
  identifier: string,
  fmt: "csv" | "json",
  relationName?: string
): Promise<void> {
  const url = buildEntityEndpointUrl(apiUrl, "/export", {
    identifier,
    fmt,
    relation_name: relationName,
    max_records: 5000,
  });
  const resp = await fetch(url, {
    method: "GET",
    headers: authToken ? { Authorization: `Bearer ${authToken}` } : {},
  });
  if (!resp.ok) {
    throw new Error(`Export failed (${resp.status})`);
  }
  const blob = await resp.blob();
  const objUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objUrl;
  anchor.download = `${identifier.replace(/[^a-zA-Z0-9_-]+/g, "_")}.${fmt}`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(objUrl);
}
