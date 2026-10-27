import type { Edge, Node } from '@xyflow/react';

export const TOULMIN_ROLES = ['grounds', 'claim', 'warrant', 'backing', 'qualifier', 'rebuttal'] as const;
export type ToulminRole = typeof TOULMIN_ROLES[number];
export type ToulminNodeType = ToulminRole;
export type ToulminEdgeKind = 'support' | 'warrant' | 'backing' | 'qualifier' | 'rebuttal';

export interface ToulminComponentItem {
  id?: string;
  proposition_id: string;
  text: string;
  source?: Record<string, unknown> | null;
  source_references?: string[];
  assertion_origin?: string;
  review_status?: string;
  provenance?: Record<string, unknown>;
}

export interface ToulminComponent {
  status?: string;
  items?: ToulminComponentItem[];
}

export interface ToulminCard {
  id: string;
  relation_id: string;
  claim_proposition_id: string;
  review_status?: string;
  claim?: ToulminComponent;
  grounds?: ToulminComponent;
  warrant?: ToulminComponent;
  backing?: ToulminComponent;
  qualifier?: ToulminComponent;
  rebuttal?: ToulminComponent;
  inference_scheme?: {
    scheme_id?: string;
    description?: string;
    conclusion_id?: string;
    premise_ids?: string[];
  };
  [key: string]: unknown;
}

export interface AifGraphNode {
  id: string;
  node_type: string;
  text?: string;
  proposition_id?: string | null;
  relation_id?: string | null;
  metadata?: Record<string, unknown>;
}

export interface AifGraphEdge {
  id: string;
  source_node_id: string;
  target_node_id: string;
}

export interface AifGraph {
  nodes: AifGraphNode[];
  edges: AifGraphEdge[];
}

export interface ToulminNodeData extends Record<string, unknown> {
  role: ToulminNodeType;
  label: string;
  text: string;
  fullText: string;
  sourceNodeId: string;
  propositionId?: string;
  relationId?: string;
  reviewStatus: string;
  origin: string;
  sourceReference?: Record<string, unknown> | null;
  schemeId?: string;
  selectedArgumentId: string;
  groundPortCount?: number;
  backingPortCount?: number;
  claimPortCount?: number;
  warrantPortCount?: number;
  qualifierPortCount?: number;
  rebuttalPortCount?: number;
  inferencePortCount?: number;
}

export interface ToulminEdgeData extends Record<string, unknown> {
  kind: ToulminEdgeKind;
  label?: string;
  originalEdgeIds: string[];
  originalRelationshipId?: string | null;
  originalSourceNodeIds?: string[];
  originalTargetNodeIds?: string[];
  semanticInferenceId?: string | null;
  relationshipType?: string;
  derived?: boolean;
  routingIndex?: number;
  routePoints?: Array<{ x: number; y: number }>;
  labelPosition?: { x: number; y: number };
  routeKey?: string;
  selectedArgumentId: string;
  isRelated?: boolean;
  isMuted?: boolean;
}

export type ToulminFlowNode = Node<ToulminNodeData, ToulminNodeType>;
export type ToulminFlowEdge = Edge<ToulminEdgeData, 'toulmin'> & { data: ToulminEdgeData };

export interface ToulminGraphPayload {
  key: string;
  graph: AifGraph;
  card: ToulminCard;
  selectedRa?: AifGraphNode | null;
}

export interface ToulminLayoutResult {
  nodes: ToulminFlowNode[];
  edges: ToulminFlowEdge[];
  width: number;
  height: number;
}

export interface ToulminInteractionApi {
  autoLayout: () => void;
  fitView: () => void;
  zoomIn: () => void;
  zoomOut: () => void;
  resetLayout: () => void;
}

declare global {
  interface Window {
    renderToulminReactFlow?: (payload: ToulminGraphPayload) => void;
    pendingToulminReactFlow?: ToulminGraphPayload;
    onToulminNodeSelected?: (node: ToulminNodeData) => void;
    onToulminEdgeSelected?: (edge: ToulminEdgeData) => void;
    toulminGraphControls?: {
      autoLayout: () => void;
      fitView: () => void;
      zoomIn: () => void;
      zoomOut: () => void;
      resetLayout: () => void;
    };
  }
}
