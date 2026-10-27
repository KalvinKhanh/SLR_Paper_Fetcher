import type { AifFlowEdge, AifFlowNode } from '../types/aif.types';

export function getFocusNodes(nodes: AifFlowNode[], edges: AifFlowEdge[], focusedNodeId?: string | null) {
  if (!focusedNodeId) return nodes;
  const ids = new Set([focusedNodeId]);
  edges.forEach((edge) => {
    if (edge.source === focusedNodeId || edge.target === focusedNodeId) {
      ids.add(edge.source);
      ids.add(edge.target);
    }
  });
  return nodes.filter((node) => ids.has(node.id));
}
