import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useEdgesState, useNodesState } from '@xyflow/react';
import type { NodeChange, EdgeChange } from '@xyflow/react';
import type { ToulminFlowEdge, ToulminFlowNode, ToulminGraphPayload } from '../types/toulmin.types';
import { adaptToulminGraph } from '../utils/graphAdapter';

export function useToulminGraph(payload: ToulminGraphPayload) {
  const [initial] = useState(() => adaptToulminGraph(payload));
  const [nodes, setNodes, onNodesChangeBase] = useNodesState<ToulminFlowNode>(initial.nodes);
  const [edges, setEdges, onEdgesChangeBase] = useEdgesState<ToulminFlowEdge>(initial.edges);
  const [graphKey, setGraphKey] = useState(payload.key);
  const [layoutRequest, setLayoutRequest] = useState(0);
  const lastPayloadKey = useRef(payload.key);

  useEffect(() => {
    if (lastPayloadKey.current === payload.key) return;
    lastPayloadKey.current = payload.key;
    const graph = adaptToulminGraph(payload);
    setNodes(graph.nodes);
    setEdges(graph.edges);
    setGraphKey(payload.key);
    setLayoutRequest((value) => value + 1);
  }, [payload, setEdges, setNodes]);

  const onNodesChange = useCallback((changes: NodeChange<ToulminFlowNode>[]) => onNodesChangeBase(changes), [onNodesChangeBase]);
  const onEdgesChange = useCallback((changes: EdgeChange<ToulminFlowEdge>[]) => onEdgesChangeBase(changes), [onEdgesChangeBase]);
  const autoLayout = useCallback(() => setLayoutRequest((value) => value + 1), []);
  const focusIds = useMemo(() => new Set<string>(), []);

  return { nodes, setNodes, edges, setEdges, onNodesChange, onEdgesChange, graphKey, layoutRequest, autoLayout, focusIds };
}
