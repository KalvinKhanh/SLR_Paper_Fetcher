import { useEffect, useRef, useState } from 'react';
import { useNodesInitialized, useReactFlow } from '@xyflow/react';
import type { AifFlowEdge, AifFlowNode } from '../types/aif.types';
import { layoutAifGraph } from '../layout/aifElkLayout';
import { getFocusNodes } from '../utils/focusNodes';

type PendingFit = {
  token: string;
  focusNodeId: string | null;
  positions: Map<string, { x: number; y: number }>;
  edgeIds: Set<string>;
};

export function useAifLayout(
  graphKey: string,
  request: number,
  nodes: AifFlowNode[],
  edges: AifFlowEdge[],
  focusedNodeId: string | null,
  setNodes: React.Dispatch<React.SetStateAction<AifFlowNode[]>>,
  setEdges: React.Dispatch<React.SetStateAction<AifFlowEdge[]>>,
) {
  const initialized = useNodesInitialized();
  const { fitView, getZoom } = useReactFlow<AifFlowNode, AifFlowEdge>();
  const sequence = useRef(0);
  const applied = useRef('');
  const fitStarted = useRef('');
  const current = useRef({ nodes, edges });
  const initialFocus = useRef(focusedNodeId);
  const [pendingFit, setPendingFit] = useState<PendingFit | null>(null);
  const [isLayingOut, setIsLayingOut] = useState(true);
  current.current = { nodes, edges };

  useEffect(() => {
    if (!nodes.length) return;
    const token = `${graphKey}:${request}`;
    if (applied.current === token) return;
    applied.current = token;
    initialFocus.current = focusedNodeId;
    const currentSequence = ++sequence.current;
    setIsLayingOut(true);
    setPendingFit(null);
    void layoutAifGraph(current.current.nodes, current.current.edges).then((layout) => {
      if (currentSequence !== sequence.current) return;
      setNodes(layout.nodes);
      setEdges(layout.edges);
      setPendingFit({
        token,
        focusNodeId: initialFocus.current,
        positions: new Map(layout.nodes.map((node) => [node.id, node.position])),
        edgeIds: new Set(layout.edges.map((edge) => edge.id)),
      });
    }).catch((error: unknown) => {
      if (currentSequence !== sequence.current) return;
      applied.current = '';
      setPendingFit(null);
      setIsLayingOut(false);
      window.dispatchEvent(new CustomEvent('aif-layout-error', { detail: error instanceof Error ? error.message : 'AIF layout failed.' }));
    });
    return () => { sequence.current++; };
  }, [fitView, graphKey, nodes.length, request, setEdges, setNodes]);

  useEffect(() => {
    if (!pendingFit || !initialized || fitStarted.current === pendingFit.token) return;
    const positionsApplied = nodes.length === pendingFit.positions.size && nodes.every((node) => {
      const expected = pendingFit.positions.get(node.id);
      return expected && Math.abs(node.position.x - expected.x) < 0.5 && Math.abs(node.position.y - expected.y) < 0.5;
    });
    const edgesApplied = edges.length === pendingFit.edgeIds.size && edges.every((edge) => pendingFit.edgeIds.has(edge.id));
    if (!positionsApplied || !edgesApplied) return;
    fitStarted.current = pendingFit.token;
    const frameNodes = nodes.length > 36 ? getFocusNodes(nodes, edges, pendingFit.focusNodeId) : nodes;
    requestAnimationFrame(() => {
      void fitView({ nodes: frameNodes, padding: 0.18, duration: 180 }).finally(() => {
        if (pendingFit.token !== `${graphKey}:${request}`) return;
        window.dispatchEvent(new CustomEvent('aif-viewport-move', { detail: { zoom: getZoom(), interactive: false } }));
        setPendingFit(null);
        setIsLayingOut(false);
      });
    });
  }, [edges, fitView, getZoom, graphKey, initialized, nodes, pendingFit, request]);

  return isLayingOut;
}
