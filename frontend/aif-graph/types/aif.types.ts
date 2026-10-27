import type { Edge, Node, Position } from '@xyflow/react';

export type AifNodeType = 'information' | 'inference' | 'conflict' | 'preference';
export type AifRelationshipKind = 'premise' | 'conclusion' | 'conflict' | 'preference' | 'related';
export type AifInformationRole = 'premise' | 'conclusion' | 'premise-conclusion' | 'information';

export interface AifRawNode {
  id: string;
  node_type: string;
  text?: string;
  proposition_id?: string | null;
  relation_id?: string | null;
  metadata?: Record<string, unknown>;
}

export interface AifRawEdge {
  id: string;
  source_node_id: string;
  target_node_id: string;
  [key: string]: unknown;
}

export interface AifRawGraph {
  nodes: AifRawNode[];
  edges: AifRawEdge[];
  [key: string]: unknown;
}

export interface AifHandle {
  id: string;
  side: Position.Left | Position.Right;
  offset: number;
}

export interface AifNodeData extends Record<string, unknown> {
  originalNode: AifRawNode;
  aifType: string;
  title: string;
  text: string;
  informationRole: AifInformationRole;
  premiseCount?: number;
  conclusionCount?: number;
  reviewStatus: string;
  groundedLabel?: string;
  sourceHandles: AifHandle[];
  targetHandles: AifHandle[];
  portOffsets?: Record<string, number>;
  width: number;
}

export interface AifEdgeData extends Record<string, unknown> {
  originalEdge: AifRawEdge;
  relationshipKind: AifRelationshipKind;
  routePoints?: Array<{ x: number; y: number }>;
  isMuted?: boolean;
  isHighlighted?: boolean;
}

export type AifFlowNode = Node<AifNodeData, AifNodeType>;
export type AifFlowEdge = Edge<AifEdgeData, 'aif'> & { data: AifEdgeData };

export interface AifGraphPayload {
  key: string;
  graph: AifRawGraph;
  focusedNodeId?: string | null;
}

export interface AifGraphControls {
  fitView: () => void;
  zoomIn: () => void;
  zoomOut: () => void;
  autoLayout: () => void;
  resetLayout: () => void;
}

declare global {
  interface Window {
    renderAifReactFlow?: (payload: AifGraphPayload) => void;
    pendingAifReactFlow?: AifGraphPayload;
    onAifNodeSelected?: (node: AifRawNode) => void;
    onAifEdgeSelected?: (edge: AifRawEdge) => void;
    aifGraphControls?: AifGraphControls;
  }
}
