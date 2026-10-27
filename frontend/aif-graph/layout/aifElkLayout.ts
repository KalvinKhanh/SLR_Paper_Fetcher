import type { ELK as ElkInstance, ElkExtendedEdge, ElkNode, ElkPort } from 'elkjs/lib/elk-api';
import type { AifFlowEdge, AifFlowNode } from '../types/aif.types';

let elkPromise: Promise<ElkInstance> | null = null;

async function getElk(): Promise<ElkInstance> {
  elkPromise ||= import('elkjs/lib/elk.bundled.js').then(({ default: Elk }) => new Elk());
  return elkPromise;
}

function dimensions(node: AifFlowNode) {
  const width = node.measured?.width || node.data.width;
  const text = node.data.text || '';
  const estimatedLines = text.split('\n').reduce((sum, line) => sum + Math.max(1, Math.ceil(line.length / 34)), 0);
  const estimatedHeight = node.type === 'information' ? Math.max(104, 80 + Math.min(estimatedLines, 4) * 18) : node.type === 'inference' ? node.data.width : 98;
  return { width, height: node.measured?.height || estimatedHeight };
}

function portsFor(node: AifFlowNode): ElkPort[] {
  return [
    ...node.data.targetHandles.map((port) => ({ id: `${node.id}::${port.id}`, width: 1, height: 1, layoutOptions: { 'elk.port.side': 'WEST' } })),
    ...node.data.sourceHandles.map((port) => ({ id: `${node.id}::${port.id}`, width: 1, height: 1, layoutOptions: { 'elk.port.side': 'EAST' } })),
  ];
}

function routePoints(edge: ElkExtendedEdge) {
  const section = edge.sections?.[0];
  if (!section?.startPoint || !section.endPoint) return undefined;
  return [section.startPoint, ...(section.bendPoints || []), section.endPoint].map(({ x, y }) => ({ x, y }));
}

export async function layoutAifGraph(nodes: AifFlowNode[], edges: AifFlowEdge[]) {
  if (!nodes.length) return { nodes, edges, width: 640, height: 360 };
  const elk = await getElk();
  const graph: ElkNode = {
    id: 'aif-root',
    layoutOptions: {
      'elk.algorithm': 'layered',
      'elk.direction': 'RIGHT',
      'elk.edgeRouting': 'ORTHOGONAL',
      'elk.spacing.nodeNode': '34',
      'elk.layered.spacing.nodeNodeBetweenLayers': '104',
      'elk.spacing.edgeNode': '18',
      'elk.spacing.edgeEdge': '12',
      'elk.padding': '[top=32,left=40,bottom=32,right=40]',
      'elk.layered.layering.strategy': 'LONGEST_PATH',
      'elk.layered.considerModelOrder.strategy': 'NODES_AND_EDGES',
      'elk.layered.crossingMinimization.strategy': 'LAYER_SWEEP',
      'elk.layered.nodePlacement.strategy': 'BRANDES_KOEPF',
      'elk.layered.nodePlacement.bk.fixedAlignment': 'BALANCED',
      'elk.layered.nodePlacement.favorStraightEdges': 'true',
    },
    children: nodes.map((node) => ({
      id: node.id,
      ...dimensions(node),
      ports: portsFor(node),
      layoutOptions: { 'elk.portConstraints': 'FIXED_ORDER' },
    })),
    edges: edges.map((edge) => ({
      id: edge.id,
      sources: [`${edge.source}::${edge.sourceHandle}`],
      targets: [`${edge.target}::${edge.targetHandle}`],
    })),
  };
  const result = await elk.layout(graph);
  const byId = new Map((result.children || []).map((node) => [node.id, node]));
  const laidOutNodes = nodes.map((node) => {
    const elkNode = byId.get(node.id);
    if (!elkNode) return node;
    const portOffsets: Record<string, number> = {};
    for (const port of elkNode.ports || []) {
      if (elkNode.height) portOffsets[port.id.slice(node.id.length + 2)] = ((port.y || 0) + (port.height || 0) / 2) / elkNode.height;
    }
    return {
      ...node,
      position: { x: elkNode.x || 0, y: elkNode.y || 0 },
      width: elkNode.width,
      height: elkNode.height,
      style: { ...node.style, width: elkNode.width, height: elkNode.height },
      data: { ...node.data, portOffsets },
    };
  });
  const laidOutEdges = edges.map((edge) => {
    const elkEdge = (result.edges || []).find((item) => item.id === edge.id);
    return elkEdge ? { ...edge, data: { ...edge.data, routePoints: routePoints(elkEdge) } } : edge;
  });
  return { nodes: laidOutNodes, edges: laidOutEdges, width: result.width || 640, height: result.height || 360 };
}
