import { memo, useCallback, useEffect, useMemo, useState } from 'react';
import type { MouseEvent as ReactMouseEvent } from 'react';
import { Background, BackgroundVariant, Controls, ReactFlow, ReactFlowProvider, useReactFlow } from '@xyflow/react';
import type { ToulminFlowEdge, ToulminFlowNode, ToulminGraphPayload, ToulminInteractionApi } from './types/toulmin.types';
import { useToulminGraph } from './hooks/useToulminGraph';
import { useAutoLayout } from './hooks/useAutoLayout';
import ClaimNode from './nodes/ClaimNode';
import GroundsNode from './nodes/GroundsNode';
import WarrantNode from './nodes/WarrantNode';
import BackingNode from './nodes/BackingNode';
import QualifierNode from './nodes/QualifierNode';
import RebuttalNode from './nodes/RebuttalNode';
import ToulminEdge from './edges/ToulminEdge';
import ToulminGraphToolbar from './ToulminGraphToolbar';

const nodeTypes = {
  grounds: GroundsNode,
  claim: ClaimNode,
  warrant: WarrantNode,
  backing: BackingNode,
  qualifier: QualifierNode,
  rebuttal: RebuttalNode,
};
const edgeTypes = { toulmin: ToulminEdge };

function GraphCanvas({ payload }: { payload: ToulminGraphPayload }) {
  const graph = useToulminGraph(payload);
  const { fitView, zoomIn, zoomOut } = useReactFlow<ToulminFlowNode, ToulminFlowEdge>();
  const [focusedNodeId, setFocusedNodeId] = useState<string | null>(null);
  const activeNavigatorNodeId = focusedNodeId || graph.nodes.find((node) => node.data.role === 'claim')?.id || null;
  const [layoutError, setLayoutError] = useState('');
  const onLayoutReady = useCallback(() => setLayoutError(''), []);
  const onLayoutError = useCallback((message: string) => setLayoutError(message), []);
  const { isLayingOut } = useAutoLayout({
    nodes: graph.nodes,
    edges: graph.edges,
    setNodes: graph.setNodes,
    setEdges: graph.setEdges,
    graphKey: graph.graphKey,
    request: graph.layoutRequest,
    onReady: onLayoutReady,
    onError: onLayoutError,
  });

  const fit = useCallback(() => {
    void fitView({
      padding: { top: '64px', right: '48px', bottom: '40px', left: '48px' },
      minZoom: graph.nodes.length > 12 ? 0.16 : 0.5,
      duration: 180,
    });
  }, [fitView, graph.nodes.length]);
  const zoomInAction = useCallback(() => { void zoomIn({ duration: 120 }); }, [zoomIn]);
  const zoomOutAction = useCallback(() => { void zoomOut({ duration: 120 }); }, [zoomOut]);
  const actions: ToulminInteractionApi = useMemo(() => ({
    autoLayout: graph.autoLayout,
    fitView: fit,
    zoomIn: zoomInAction,
    zoomOut: zoomOutAction,
    resetLayout: graph.autoLayout,
  }), [fit, graph.autoLayout, zoomInAction, zoomOutAction]);

  useEffect(() => {
    window.toulminGraphControls = actions;
    return () => { if (window.toulminGraphControls === actions) delete window.toulminGraphControls; };
  }, [actions]);

  const relatedNodeIds = useMemo(() => {
    if (!focusedNodeId) return null;
    const related = new Set([focusedNodeId]);
    for (const edge of graph.edges) if (edge.source === focusedNodeId || edge.target === focusedNodeId) {
      related.add(edge.source);
      related.add(edge.target);
    }
    return related;
  }, [focusedNodeId, graph.edges]);
  const nodes = useMemo<ToulminFlowNode[]>(() => graph.nodes.map((node) => ({
    ...node,
    className: relatedNodeIds && !relatedNodeIds.has(node.id) ? 'is-muted' : '',
  })), [graph.nodes, relatedNodeIds]);
  const edges = useMemo<ToulminFlowEdge[]>(() => graph.edges.map((edge) => {
    const related = !relatedNodeIds || (relatedNodeIds.has(edge.source) && relatedNodeIds.has(edge.target));
    return { ...edge, data: { ...edge.data, isRelated: related, isMuted: !related } };
  }), [graph.edges, relatedNodeIds]);

  const onNodeClick = useCallback((_event: ReactMouseEvent, node: ToulminFlowNode) => {
    setFocusedNodeId(node.id);
    window.onToulminNodeSelected?.(node.data);
  }, []);
  const onNavigateNode = useCallback((node: ToulminFlowNode) => {
    setFocusedNodeId(node.id);
    window.onToulminNodeSelected?.(node.data);
    // Finding/Conclusion navigation changes focus, not graph scope; keep the complete
    // argument map in view so tab selection never strands the rest of the structure.
    fit();
  }, [fit]);
  const onEdgeClick = useCallback((_event: ReactMouseEvent, edge: ToulminFlowEdge) => {
    setFocusedNodeId(edge.source);
    if (edge.data) window.onToulminEdgeSelected?.(edge.data);
  }, []);
  const onPaneClick = useCallback(() => setFocusedNodeId(null), []);
  const onMoveEnd = useCallback((_event: MouseEvent | TouchEvent | null, viewport: { zoom: number }) => {
    const level = document.getElementById('aifZoomLevel');
    if (level) level.textContent = `${Math.round(viewport.zoom * 100)}%`;
  }, []);

  return (
    <div className="toulmin-flow-shell" aria-label="Toulmin argument graph">
      <ReactFlow<ToulminFlowNode, ToulminFlowEdge>
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={graph.onNodesChange}
        onEdgesChange={graph.onEdgesChange}
        onNodeClick={onNodeClick}
        onEdgeClick={onEdgeClick}
        onPaneClick={onPaneClick}
        onMoveEnd={onMoveEnd}
        nodesDraggable
        nodesConnectable={false}
        elementsSelectable
        edgesFocusable
        nodesFocusable
        panOnDrag
        zoomOnScroll
        zoomOnPinch
        minZoom={0.16}
        maxZoom={1.6}
        // These role cards start at the same provisional position until ELK resolves.
        // Keep them mounted so React Flow measures every card before the first fit.
        onlyRenderVisibleElements={false}
        deleteKeyCode={null}
        proOptions={{ hideAttribution: true }}
        className="toulmin-flow"
        aria-label="Interactive Toulmin graph. Drag nodes to reposition. Use the mouse wheel to zoom and drag the canvas to pan."
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="#d8e0e7" />
        <Controls showInteractive={false} position="bottom-right" />
        <ToulminGraphToolbar nodes={graph.nodes} activeNodeId={activeNavigatorNodeId} onAutoLayout={graph.autoLayout} onFitView={fit} onSelectNode={onNavigateNode} />
      </ReactFlow>
      {layoutError ? <div className="toulmin-flow-error" role="alert">Layout failed: {layoutError}</div> : null}
      {isLayingOut ? <div className="toulmin-flow-loading" role="status">Arranging argument graph…</div> : null}
    </div>
  );
}

const GraphCanvasMemo = memo(GraphCanvas);

export function ToulminGraph({ payload }: { payload: ToulminGraphPayload }) {
  return <ReactFlowProvider><GraphCanvasMemo payload={payload} /></ReactFlowProvider>;
}

export default memo(ToulminGraph);
