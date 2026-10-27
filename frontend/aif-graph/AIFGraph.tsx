import { memo, useCallback, useEffect, useMemo, useState } from 'react';
import { Background, BackgroundVariant, Controls, ReactFlow, ReactFlowProvider, useReactFlow } from '@xyflow/react';
import type { AifFlowEdge, AifFlowNode, AifGraphPayload } from './types/aif.types';
import { useAifGraph } from './hooks/useAifGraph';
import { useAifLayout } from './hooks/useAifLayout';
import { graphKey } from './adapters/aifGraphAdapter';
import InformationNode from './nodes/InformationNode';
import InferenceNode from './nodes/InferenceNode';
import ConflictNode from './nodes/ConflictNode';
import PreferenceNode from './nodes/PreferenceNode';
import AifEdge from './edges/AifEdge';
import { getFocusNodes } from './utils/focusNodes';

const nodeTypes = { information: InformationNode, inference: InferenceNode, conflict: ConflictNode, preference: PreferenceNode };
const edgeTypes = { aif: AifEdge };

function GraphCanvas({ payload }: { payload: AifGraphPayload }) {
  const graph = useAifGraph(payload);
  const [focusedNodeId, setFocusedNodeId] = useState<string | null>(payload.focusedNodeId || null);
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const [layoutError, setLayoutError] = useState('');
  const isLayingOut = useAifLayout(graph.graphKey, graph.layoutRequest, graph.nodes, graph.edges, focusedNodeId || payload.focusedNodeId || null, graph.setNodes, graph.setEdges);
  const { fitView, zoomIn, zoomOut } = useReactFlow<AifFlowNode, AifFlowEdge>();
  const activeNodeId = hoveredNodeId || focusedNodeId;
  const connected = useMemo(() => {
    if (!activeNodeId) return null;
    const ids = new Set([activeNodeId]);
    graph.edges.forEach((edge) => {
      if (edge.source === activeNodeId || edge.target === activeNodeId) { ids.add(edge.source); ids.add(edge.target); }
    });
    return ids;
  }, [activeNodeId, graph.edges]);
  const nodes = useMemo(() => graph.nodes.map((node) => ({
    ...node,
    className: [connected && !connected.has(node.id) ? 'is-muted' : '', activeNodeId === node.id ? 'is-focused' : ''].filter(Boolean).join(' '),
  })), [activeNodeId, connected, graph.nodes]);
  const edges = useMemo(() => graph.edges.map((edge) => ({ ...edge, className: activeNodeId && (edge.source === activeNodeId || edge.target === activeNodeId) ? 'is-highlighted' : '', data: {
    ...edge.data,
    isMuted: Boolean(connected && !(connected.has(edge.source) && connected.has(edge.target))),
    isHighlighted: Boolean(activeNodeId && (edge.source === activeNodeId || edge.target === activeNodeId)),
  } })), [activeNodeId, connected, graph.edges]);
  const fit = useCallback(() => {
    const nodesToFit = graph.nodes.length > 36 ? getFocusNodes(graph.nodes, graph.edges, focusedNodeId || payload.focusedNodeId) : graph.nodes;
    void fitView({ nodes: nodesToFit, padding: 0.18, duration: 180 });
  }, [fitView, focusedNodeId, graph.edges, graph.nodes, payload.focusedNodeId]);
  const relayout = useCallback(() => { graph.autoLayout(); }, [graph.autoLayout]);

  useEffect(() => {
    window.aifGraphControls = { fitView: fit, zoomIn: () => { void zoomIn({ duration: 120 }); }, zoomOut: () => { void zoomOut({ duration: 120 }); }, autoLayout: relayout, resetLayout: relayout };
    return () => { delete window.aifGraphControls; };
  }, [fit, relayout, zoomIn, zoomOut]);

  useEffect(() => {
    const onLayoutError = (event: Event) => setLayoutError(String((event as CustomEvent<string>).detail || 'AIF layout failed.'));
    window.addEventListener('aif-layout-error', onLayoutError);
    return () => window.removeEventListener('aif-layout-error', onLayoutError);
  }, []);

  const onNodeClick = useCallback((_event: React.MouseEvent, node: AifFlowNode) => {
    setFocusedNodeId(node.id);
    window.onAifNodeSelected?.(node.data.originalNode);
  }, []);
  const onNodeDoubleClick = useCallback((_event: React.MouseEvent, node: AifFlowNode) => {
    void fitView({ nodes: [{ id: node.id }], padding: 0.55, duration: 180 });
    setFocusedNodeId(node.id);
  }, [fitView]);
  const onNodeMouseEnter = useCallback((_event: React.MouseEvent, node: AifFlowNode) => setHoveredNodeId(node.id), []);
  const onNodeMouseLeave = useCallback(() => setHoveredNodeId(null), []);
  const onMove = useCallback((_event: MouseEvent | TouchEvent | null, viewport: { x: number; y: number; zoom: number }) => {
    window.dispatchEvent(new CustomEvent('aif-viewport-move', { detail: { zoom: viewport.zoom, interactive: !isLayingOut } }));
  }, [isLayingOut]);
  const onEdgeClick = useCallback((_event: React.MouseEvent, edge: AifFlowEdge) => {
    window.onAifEdgeSelected?.(edge.data.originalEdge);
  }, []);
  const onPaneClick = useCallback(() => { setFocusedNodeId(null); setHoveredNodeId(null); }, []);

  return <div className="aif-flow-shell" aria-label="AIF argument graph">
    <ReactFlow<AifFlowNode, AifFlowEdge>
      nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
      onNodesChange={graph.onNodesChange} onEdgesChange={graph.onEdgesChange}
      onNodeClick={onNodeClick} onNodeDoubleClick={onNodeDoubleClick} onNodeMouseEnter={onNodeMouseEnter} onNodeMouseLeave={onNodeMouseLeave}
      onEdgeClick={onEdgeClick} onPaneClick={onPaneClick} onMove={onMove}
      nodesDraggable nodesConnectable={false} elementsSelectable edgesFocusable nodesFocusable
      panOnDrag zoomOnScroll zoomOnPinch minZoom={0.08} maxZoom={1.8} onlyRenderVisibleElements={graph.nodes.length > 300 && !isLayingOut}
      deleteKeyCode={null} proOptions={{ hideAttribution: true }} className="aif-flow"
      aria-label="Interactive AIF graph. Drag nodes to reposition. Use the mouse wheel to zoom and drag the canvas to pan."
    >
      <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="#d8e0e7" />
      <Controls showInteractive={false} position="bottom-right" />
    </ReactFlow>
    {isLayingOut ? <div className="aif-flow-loading" role="status">Arranging AIF graph…</div> : null}
    {layoutError ? <div className="aif-flow-warning" role="alert">Automatic layout failed: {layoutError}</div> : null}
    {graph.droppedEdgeIds.length ? <div className="aif-flow-warning" role="status">{graph.droppedEdgeIds.length} relationship(s) refer to missing AIF nodes and were omitted from this view.</div> : null}
  </div>;
}

const GraphCanvasMemo = memo(GraphCanvas);

export function AIFGraph({ payload }: { payload: AifGraphPayload }) {
  return <ReactFlowProvider><GraphCanvasMemo key={graphKey(payload)} payload={payload} /></ReactFlowProvider>;
}

export default memo(AIFGraph);
