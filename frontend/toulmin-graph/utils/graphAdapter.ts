import { MarkerType } from '@xyflow/react';
import type {
  AifGraph, AifGraphEdge, AifGraphNode, ToulminCard, ToulminComponentItem,
  ToulminEdgeData, ToulminFlowEdge, ToulminFlowNode, ToulminGraphPayload, ToulminRole,
} from '../types/toulmin.types';
import { evidenceColumnCount } from '../layout/elkLayout';

interface AdaptedGraph { nodes: ToulminFlowNode[]; edges: ToulminFlowEdge[] }

const ROLE_LABELS: Record<ToulminRole, string> = {
  grounds: 'Grounds · Data', claim: 'Claim · Conclusion', warrant: 'Warrant', backing: 'Backing', qualifier: 'Qualifier', rebuttal: 'Rebuttal',
};

const EDGE_COLORS: Record<ToulminEdgeData['kind'], string> = {
  support: '#256b83', warrant: '#8a5b00', backing: '#6b46a1', qualifier: '#2463a8', rebuttal: '#b33636',
};

function visualNodeId(role: ToulminRole, sourceId: string, used: Set<string>): string {
  if (!used.has(sourceId)) { used.add(sourceId); return sourceId; }
  let duplicate = `${sourceId}::toulmin:${role}`;
  let index = 2;
  while (used.has(duplicate)) duplicate = `${sourceId}::toulmin:${role}:${index++}`;
  used.add(duplicate);
  return duplicate;
}

function makeRoleNode(role: ToulminRole, item: ToulminComponentItem, source: AifGraphNode, card: ToulminCard, used: Set<string>): ToulminFlowNode {
  const metadata = source.metadata || {};
  return {
    id: visualNodeId(role, source.id, used), type: role, position: { x: 0, y: 0 }, draggable: true, style: { width: 284 },
    data: {
      role, label: ROLE_LABELS[role], text: item.text || source.text || '', fullText: item.text || source.text || '',
      sourceNodeId: source.id, propositionId: item.proposition_id, relationId: source.relation_id || card.relation_id,
      reviewStatus: item.review_status || String(metadata.review_status || card.review_status || 'pending'),
      origin: item.assertion_origin || String(metadata.assertion_origin || (metadata.is_inferred ? 'inferred' : 'source')),
      sourceReference: item.source || null, selectedArgumentId: card.id,
    },
  };
}

function getComponents(card: ToulminCard): Map<ToulminRole, ToulminComponentItem[]> {
  const roles: ToulminRole[] = ['grounds', 'claim', 'warrant', 'backing', 'qualifier', 'rebuttal'];
  return new Map(roles.map((role) => [role, (card[role]?.items || []).filter((item) => Boolean(item?.proposition_id && item?.text))]));
}

function traceOriginalEdges(raw: AifGraphEdge[], sourceId: string, targetId: string, inferenceId?: string): { ids: string[]; derived: boolean } {
  const direct = raw.filter((edge) => edge.source_node_id === sourceId && edge.target_node_id === targetId);
  if (direct.length) return { ids: direct.map((edge) => edge.id), derived: false };
  if (!inferenceId) return { ids: [], derived: true };
  const incoming = raw.filter((edge) => edge.source_node_id === sourceId && edge.target_node_id === inferenceId);
  const outgoing = raw.filter((edge) => edge.source_node_id === inferenceId && edge.target_node_id === targetId);
  // AIF commonly records both Grounds and Warrant as premises pointing into RA.
  const targetPremise = raw.filter((edge) => edge.source_node_id === targetId && edge.target_node_id === inferenceId);
  return { ids: [...incoming, ...outgoing, ...targetPremise].map((edge) => edge.id), derived: true };
}

function makeEdge(args: {
  id: string; source: ToulminFlowNode; target: ToulminFlowNode; kind: ToulminEdgeData['kind']; cardId: string;
  rawEdges: AifGraphEdge[]; inferenceId?: string; sourceHandle: string; targetHandle: string; label?: string;
}): ToulminFlowEdge {
  const { id, source, target, kind, cardId, rawEdges, inferenceId, sourceHandle, targetHandle, label } = args;
  const trace = traceOriginalEdges(rawEdges, source.data.sourceNodeId, target.data.sourceNodeId, inferenceId);
  const relationshipType = kind === 'support' ? 'supports' : kind === 'warrant' ? 'justifies_reasoning'
    : kind === 'backing' ? 'supports_warrant' : kind === 'qualifier' ? 'qualifies' : 'challenges';
  return {
    id, source: source.id, target: target.id, type: 'toulmin', sourceHandle, targetHandle,
    // Edges sit one layer above node cards so the arrow tip remains visible on the card border.
    zIndex: 1,
    // Mark only the target end and inherit the relationship color; this keeps arrow direction
    // obvious even where several relationship types meet the Claim.
    markerEnd: { type: MarkerType.ArrowClosed, color: EDGE_COLORS[kind], width: 16, height: 16, markerUnits: 'userSpaceOnUse', orient: 'auto', strokeWidth: 1.6 },
    data: {
      kind, label, relationshipType, originalEdgeIds: trace.ids, originalRelationshipId: trace.ids[0] || null,
      originalSourceNodeIds: [source.data.sourceNodeId], originalTargetNodeIds: [target.data.sourceNodeId],
      semanticInferenceId: inferenceId || null, derived: trace.derived, routeKey: id, selectedArgumentId: cardId,
    },
  };
}

export function adaptToulminGraph(payload: ToulminGraphPayload): AdaptedGraph {
  const { graph, card, selectedRa } = payload;
  const aifNodes = new Map((graph.nodes || []).map((node) => [node.id, node]));
  const groups = getComponents(card);
  const usedIds = new Set<string>();
  const nodes: ToulminFlowNode[] = [];
  const roles = new Map<ToulminRole, ToulminFlowNode[]>();
  for (const role of ['grounds', 'claim', 'warrant', 'backing', 'qualifier', 'rebuttal'] as const) {
    const items: ToulminFlowNode[] = [];
    for (const item of groups.get(role) || []) {
      const sourceId = `i:${item.proposition_id}`;
      const source = aifNodes.get(sourceId) || { id: sourceId, node_type: 'I', proposition_id: item.proposition_id, text: item.text, metadata: {} };
      const node = makeRoleNode(role, item, source, card, usedIds);
      items.push(node); nodes.push(node);
    }
    roles.set(role, items);
  }

  // RA remains a real AIF inference entity. It is intentionally not a React Flow node;
  // direct Toulmin presentation edges retain the RA and original AIF path for inspection.
  const relation = selectedRa || (graph.nodes || []).find((node) => node.relation_id === card.relation_id && node.node_type === 'RA');
  const inferenceId = relation?.id || `ra:${card.relation_id}`;
  const grounds = roles.get('grounds') || [];
  const claims = roles.get('claim') || [];
  const warrants = roles.get('warrant') || [];
  const backings = roles.get('backing') || [];
  const qualifiers = roles.get('qualifier') || [];
  const rebuttals = roles.get('rebuttal') || [];
  const primaryClaim = claims.find((node) => node.data.propositionId === card.claim_proposition_id) || claims[0];
  const rawEdges = graph.edges || [];
  const edges: ToulminFlowEdge[] = [];

  // Each evidence node visibly supports the claim; the original Grounds→RA→Claim path is traceable.
  const groundsColumns = evidenceColumnCount(grounds.length);
  const outerGroundOrder = new Map<string, number>();
  const outerGrounds = grounds.map((ground, index) => ({ ground, index, column: index % groundsColumns }))
    .filter(({ column }) => column < groundsColumns - 1);
  const orderedOuterGrounds = [
    ...outerGrounds.filter(({ column }) => column > 0),
    ...outerGrounds.filter(({ column }) => column === 0),
  ];
  orderedOuterGrounds.forEach(({ ground }, rank) => outerGroundOrder.set(ground.id, rank));
  grounds.forEach((ground, index) => {
    const target = claims.find((claim) => rawEdges.some((edge) => edge.source_node_id === inferenceId && edge.target_node_id === claim.data.sourceNodeId)) || primaryClaim;
    if (!target) return;
    const column = index % groundsColumns;
    const sourceHandle = column === 0 ? 'out-left' : column === groundsColumns - 1 ? 'out-right' : 'out-bottom';
    const targetHandle = sourceHandle === 'out-right' ? `in-ground-${index}` : `in-ground-outer-${outerGroundOrder.get(ground.id) ?? index}`;
    edges.push(makeEdge({ id: `tl:${card.id}:supports:${ground.id}:${target.id}`, source: ground, target, kind: 'support', cardId: card.id, rawEdges, inferenceId, sourceHandle, targetHandle, label: index === 0 ? 'Supports claim' : undefined }));
  });

  warrants.forEach((warrant, index) => {
    const target = primaryClaim;
    if (!target) return;
    edges.push(makeEdge({ id: `tl:${card.id}:warrants:${warrant.id}:${target.id}`, source: warrant, target, kind: 'warrant', cardId: card.id, rawEdges, inferenceId, sourceHandle: index === 0 ? 'out-top' : 'out-right', targetHandle: `in-warrant-${index}`, label: index === 0 ? 'Justifies reasoning' : undefined }));
  });

  const backingCounts = new Map<string, number>();
  backings.forEach((backing, index) => {
    const target = warrants[Math.min(index, Math.max(warrants.length - 1, 0))];
    if (!target) return;
    const port = backingCounts.get(target.id) || 0;
    backingCounts.set(target.id, port + 1);
    target.data.backingPortCount = port + 1;
    const needsBypassLane = backings.length > 1 || warrants.length > 1;
    edges.push(makeEdge({ id: `tl:${card.id}:backs:${backing.id}:${target.id}`, source: backing, target, kind: 'backing', cardId: card.id, rawEdges, inferenceId, sourceHandle: needsBypassLane ? 'out-right' : 'out-top', targetHandle: `in-backing-${port}`, label: index === 0 ? 'Supports warrant' : undefined }));
  });

  if (primaryClaim) {
    primaryClaim.data.groundPortCount = grounds.length;
    primaryClaim.data.qualifierPortCount = qualifiers.length;
    primaryClaim.data.rebuttalPortCount = rebuttals.length;
    primaryClaim.data.warrantPortCount = warrants.length;
    qualifiers.forEach((node, index) => edges.push(makeEdge({ id: `tl:${card.id}:qualifies:${node.id}:${primaryClaim.id}`, source: node, target: primaryClaim, kind: 'qualifier', cardId: card.id, rawEdges, inferenceId, sourceHandle: 'out-right', targetHandle: `in-qualifier-${index}`, label: index === 0 ? 'Qualifies' : undefined })));
    rebuttals.forEach((node, index) => edges.push(makeEdge({ id: `tl:${card.id}:challenges:${node.id}:${primaryClaim.id}`, source: node, target: primaryClaim, kind: 'rebuttal', cardId: card.id, rawEdges, inferenceId, sourceHandle: index === 0 ? 'out-top' : 'out-right', targetHandle: `in-rebuttal-${index}`, label: index === 0 ? 'Challenges claim' : undefined })));
  }

  // Guard the adapter boundary: every rendered endpoint must be a real role node.
  const routeIndexes = new Map<string, number>();
  const bottomSupportEdges = edges.filter((edge) => edge.data.kind === 'support' && edge.sourceHandle === 'out-bottom');
  const leftSupportEdges = edges.filter((edge) => edge.data.kind === 'support' && edge.sourceHandle === 'out-left');
  const directSupportEdges = edges.filter((edge) => edge.data.kind === 'support' && edge.sourceHandle === 'out-right');
  const supportRouteIndexes = new Map<string, number>();
  bottomSupportEdges.forEach((edge, index) => supportRouteIndexes.set(edge.id, index));
  leftSupportEdges.forEach((edge, index) => supportRouteIndexes.set(edge.id, bottomSupportEdges.length + index));
  directSupportEdges.forEach((edge, index) => supportRouteIndexes.set(edge.id, index));
  for (const edge of edges) {
    const index = edge.data.kind === 'support' ? supportRouteIndexes.get(edge.id) || 0 : routeIndexes.get(edge.data.kind) || 0;
    edge.data.routingIndex = index;
    if (edge.data.kind !== 'support') routeIndexes.set(edge.data.kind, index + 1);
  }
  const nodeIds = new Set(nodes.map((node) => node.id));
  const validEdges = edges.filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target));
  return { nodes, edges: validEdges };
}

export function edgeMatchesRawAif(graph: AifGraph, sourceId: string, targetId: string): string[] {
  return (graph.edges || []).filter((edge) => edge.source_node_id === sourceId && edge.target_node_id === targetId).map((edge) => edge.id);
}
