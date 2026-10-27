import { useCallback, useEffect, useRef, useState } from 'react';
import { useEdgesState, useNodesState } from '@xyflow/react';
import type { EdgeChange, NodeChange } from '@xyflow/react';
import type { AifFlowEdge, AifFlowNode, AifGraphPayload } from '../types/aif.types';
import { adaptAifGraph } from '../adapters/aifGraphAdapter';

export function useAifGraph(payload: AifGraphPayload) {
  const [initial] = useState(() => adaptAifGraph(payload.graph));
  const [nodes, setNodes, onNodesChangeBase] = useNodesState<AifFlowNode>(initial.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState<AifFlowEdge>(initial.edges);
  const [droppedEdgeIds, setDroppedEdgeIds] = useState(initial.droppedEdges);
  const [graphKey, setGraphKey] = useState(payload.key);
  const [layoutRequest, setLayoutRequest] = useState(0);
  const lastPayloadKey = useRef(payload.key);

  useEffect(() => {
    if (lastPayloadKey.current === payload.key) return;
    lastPayloadKey.current = payload.key;
    const adapted = adaptAifGraph(payload.graph);
    setNodes(adapted.nodes);
    setEdges(adapted.edges);
    setDroppedEdgeIds(adapted.droppedEdges);
    setGraphKey(payload.key);
    setLayoutRequest((value) => value + 1);
  }, [payload, setEdges, setNodes]);

  const onNodesChange = useCallback((changes: NodeChange<AifFlowNode>[]) => {
    onNodesChangeBase(changes);
    if (changes.some((change) => change.type === 'position' && change.dragging)) {
      setEdges((current) => current.map((edge) => edge.data.routePoints ? { ...edge, data: { ...edge.data, routePoints: undefined } } : edge));
    }
  }, [onNodesChangeBase, setEdges]);
  const onEdgesChangeBase = useCallback((changes: EdgeChange<AifFlowEdge>[]) => onEdgesChange(changes), [onEdgesChange]);
  const autoLayout = useCallback(() => setLayoutRequest((value) => value + 1), []);
  return { nodes, setNodes, edges, setEdges, droppedEdgeIds, onNodesChange, onEdgesChange: onEdgesChangeBase, graphKey, layoutRequest, autoLayout };
}
