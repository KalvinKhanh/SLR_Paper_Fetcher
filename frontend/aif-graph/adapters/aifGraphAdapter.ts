import { MarkerType, Position } from '@xyflow/react';
import type { AifEdgeData, AifFlowEdge, AifFlowNode, AifGraphPayload, AifNodeData, AifNodeType, AifRawEdge, AifRawGraph, AifRawNode } from '../types/aif.types';

const TYPE_MAP: Record<string, AifNodeType> = { I: 'information', RA: 'inference', CA: 'conflict', PA: 'preference' };
const TYPE_TITLES: Record<string, string> = { I: 'Information', RA: 'Rule of Inference', CA: 'Conflict Application', PA: 'Preference Application' };

function nodeSize(node: AifRawNode) {
  if (node.node_type !== 'I') return { width: node.node_type === 'RA' ? 132 : 98, height: node.node_type === 'RA' ? 132 : 98 };
  const width = 264;
  const content = node.text || '';
  const lines = content.split('\n').reduce((count, line) => count + Math.max(1, Math.ceil(line.length / 34)), 0);
  return { width, height: Math.max(104, 80 + Math.min(lines, 4) * 18) };
}

function relationshipKind(edge: AifRawEdge, nodes: Map<string, AifRawNode>): AifEdgeData['relationshipKind'] {
  const source = nodes.get(edge.source_node_id)?.node_type;
  const target = nodes.get(edge.target_node_id)?.node_type;
  if (source === 'CA' || target === 'CA') return 'conflict';
  if (source === 'PA' || target === 'PA') return 'preference';
  if (target === 'RA') return 'premise';
  if (source === 'RA') return 'conclusion';
  return 'related';
}

export function adaptAifGraph(graph: AifRawGraph): { nodes: AifFlowNode[]; edges: AifFlowEdge[]; droppedEdges: string[] } {
  const rawNodes = graph.nodes || [];
  const byId = new Map(rawNodes.map((node) => [node.id, node]));
  const rawEdges = [...(graph.edges || [])].sort((a, b) => a.id.localeCompare(b.id));
  const out = new Map<string, AifRawEdge[]>();
  const incoming = new Map<string, AifRawEdge[]>();
  const droppedEdges: string[] = [];
  for (const edge of rawEdges) {
    if (!byId.has(edge.source_node_id) || !byId.has(edge.target_node_id)) {
      droppedEdges.push(edge.id);
      continue;
    }
    out.set(edge.source_node_id, [...(out.get(edge.source_node_id) || []), edge]);
    incoming.set(edge.target_node_id, [...(incoming.get(edge.target_node_id) || []), edge]);
  }

  const nodes: AifFlowNode[] = rawNodes.map((node) => {
    const type = TYPE_MAP[node.node_type] || 'information';
    const size = nodeSize(node);
    const sourceEdges = out.get(node.id) || [];
    const targetEdges = incoming.get(node.id) || [];
    const isPremise = node.node_type === 'I' && sourceEdges.some((edge) => byId.get(edge.target_node_id)?.node_type === 'RA');
    const isConclusion = node.node_type === 'I' && targetEdges.some((edge) => byId.get(edge.source_node_id)?.node_type === 'RA');
    const informationRole = isPremise && isConclusion ? 'premise-conclusion' : isPremise ? 'premise' : isConclusion ? 'conclusion' : 'information';
    const premiseCount = node.node_type === 'RA' ? targetEdges.filter((edge) => byId.get(edge.source_node_id)?.node_type === 'I').length : undefined;
    const conclusionCount = node.node_type === 'RA' ? sourceEdges.filter((edge) => byId.get(edge.target_node_id)?.node_type === 'I').length : undefined;
    const data: AifNodeData = {
      originalNode: node,
      aifType: node.node_type,
      title: TYPE_TITLES[node.node_type] || `${node.node_type || 'Unknown'} node`,
      text: node.text || '',
      informationRole,
      premiseCount,
      conclusionCount,
      reviewStatus: String(node.metadata?.review_status || 'pending'),
      sourceHandles: sourceEdges.map((edge, index) => ({ id: `out-${index}`, side: Position.Right, offset: (index + 1) / (sourceEdges.length + 1) })),
      targetHandles: targetEdges.map((edge, index) => ({ id: `in-${index}`, side: Position.Left, offset: (index + 1) / (targetEdges.length + 1) })),
      width: size.width,
    };
    return { id: node.id, type, position: { x: 0, y: 0 }, width: size.width, height: size.height, style: { width: size.width, height: size.height }, data };
  });

  const sourcePorts = new Map<string, number>();
  const targetPorts = new Map<string, number>();
  const edges: AifFlowEdge[] = [];
  for (const edge of rawEdges) {
    if (!byId.has(edge.source_node_id) || !byId.has(edge.target_node_id)) continue;
    const sourceIndex = sourcePorts.get(edge.source_node_id) || 0;
    const targetIndex = targetPorts.get(edge.target_node_id) || 0;
    sourcePorts.set(edge.source_node_id, sourceIndex + 1);
    targetPorts.set(edge.target_node_id, targetIndex + 1);
    const kind = relationshipKind(edge, byId);
    const color = kind === 'conflict' ? '#b84c56' : kind === 'preference' ? '#7751a8' : '#8793a2';
    edges.push({
      id: edge.id,
      source: edge.source_node_id,
      target: edge.target_node_id,
      sourceHandle: `out-${sourceIndex}`,
      targetHandle: `in-${targetIndex}`,
      type: 'aif',
      markerEnd: { type: MarkerType.ArrowClosed, width: 15, height: 15, color },
      data: { originalEdge: edge, relationshipKind: kind },
    });
  }
  return { nodes, edges, droppedEdges };
}

export function graphKey(payload: AifGraphPayload) {
  return payload.key || JSON.stringify([payload.graph.nodes, payload.graph.edges]);
}
