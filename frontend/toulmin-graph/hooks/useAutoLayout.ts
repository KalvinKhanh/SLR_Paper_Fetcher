import { useEffect, useRef, useState } from 'react';
import { useNodesInitialized, useReactFlow } from '@xyflow/react';
import type { ToulminFlowEdge, ToulminFlowNode } from '../types/toulmin.types';
import { layoutToulminGraph } from '../layout/elkLayout';

interface Options {
  nodes: ToulminFlowNode[];
  edges: ToulminFlowEdge[];
  setNodes: React.Dispatch<React.SetStateAction<ToulminFlowNode[]>>;
  setEdges: React.Dispatch<React.SetStateAction<ToulminFlowEdge[]>>;
  graphKey: string;
  request: number;
  onReady: () => void;
  onError: (message: string) => void;
}

interface PendingFit {
  token: string;
  positions: Map<string, { x: number; y: number }>;
  edgeIds: string[];
  nodeCount: number;
}

export function useAutoLayout({ nodes, edges, setNodes, setEdges, graphKey, request, onReady, onError }: Options) {
  const initialized = useNodesInitialized();
  const { fitView } = useReactFlow<ToulminFlowNode, ToulminFlowEdge>();
  const sequence = useRef(0);
  const applied = useRef('');
  const currentGraph = useRef({ nodes, edges });
  const pendingFit = useRef<PendingFit | null>(null);
  currentGraph.current = { nodes, edges };
  const [isLayingOut, setIsLayingOut] = useState(true);

  useEffect(() => {
    const allNodesMeasured = nodes.length > 0 && nodes.every((node) => node.measured?.width && node.measured?.height);
    if (!initialized || !allNodesMeasured) return;
    const token = `${graphKey}:${request}`;
    if (applied.current === token) return;
    applied.current = token;
    pendingFit.current = null;
    const current = ++sequence.current;
    setIsLayingOut(true);
    onError('');
    const snapshot = currentGraph.current;
    void layoutToulminGraph(snapshot.nodes, snapshot.edges).then((result) => {
      if (current !== sequence.current) return;
      const positions = new Map(result.nodes.map((node) => [node.id, node.position]));
      pendingFit.current = { token, positions, edgeIds: result.edges.map((edge) => edge.id), nodeCount: result.nodes.length };
      setNodes((previous) => previous.map((node) => ({ ...node, position: positions.get(node.id) || node.position })));
      setEdges(result.edges);
    }).catch((error: unknown) => {
      if (current !== sequence.current) return;
      applied.current = '';
      setIsLayingOut(false);
      onError(error instanceof Error ? error.message : 'Automatic layout failed.');
    });
    return () => { sequence.current++; };
  }, [graphKey, initialized, nodes.length, onError, request, setEdges, setNodes]);

  // Fit only after React Flow has committed the ELK positions and remeasured the cards.
  // The old single animation-frame fit could run against stale positions, leaving the
  // graph visually pinned to the upper-right of the canvas after a tab change.
  useEffect(() => {
    const pending = pendingFit.current;
    if (!pending || !initialized || nodes.length !== pending.nodeCount) return;
    const positionsCommitted = nodes.every((node) => {
      const expected = pending.positions.get(node.id);
      return !expected || (Math.abs(node.position.x - expected.x) < 0.5 && Math.abs(node.position.y - expected.y) < 0.5);
    });
    const edgeIds = edges.map((edge) => edge.id);
    const edgesCommitted = edgeIds.length === pending.edgeIds.length && pending.edgeIds.every((id) => edgeIds.includes(id));
    if (!positionsCommitted || !edgesCommitted || nodes.some((node) => !node.measured?.width || !node.measured?.height)) return;
    pendingFit.current = null;
    const current = sequence.current;
    requestAnimationFrame(() => requestAnimationFrame(() => {
      void fitView({
        padding: { top: '64px', right: '48px', bottom: '40px', left: '48px' },
        minZoom: pending.nodeCount > 12 ? 0.16 : 0.5,
        duration: 180,
      }).then(() => {
        if (current !== sequence.current) return;
        setIsLayingOut(false);
        onReady();
      });
    }));
  }, [edges, fitView, initialized, nodes, onReady]);

  return { isLayingOut };
}
