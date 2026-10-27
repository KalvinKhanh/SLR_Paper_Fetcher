import type { ELK as ElkInstance, ElkExtendedEdge, ElkNode, ElkPort } from 'elkjs/lib/elk-api';
import type { ToulminFlowEdge, ToulminFlowNode, ToulminLayoutResult } from '../types/toulmin.types';

let elkPromise: Promise<ElkInstance> | null = null;
const NODE_WIDTH = 284;
const STACK_GAP = 38;
const COLUMN_GAP = 46;
const EVIDENCE_GAP = 24;
const LABEL_HEIGHT = 24;
export const SUPPORT_LANE_START = 24;
export const SUPPORT_LANE_STEP = 12;

async function getElk(): Promise<ElkInstance> {
  elkPromise ||= import('elkjs/lib/elk.bundled.js').then(({ default: Elk }) => new Elk());
  return elkPromise;
}

function sizeOf(node: ToulminFlowNode) {
  const measured = (node as ToulminFlowNode & { measured?: { width?: number; height?: number } }).measured;
  const width = measured?.width || Number(node.style?.width) || NODE_WIDTH;
  const text = node.data.fullText || node.data.text || '';
  return { width, height: measured?.height || Number(node.style?.height) || Math.max(112, 74 + Math.max(2, Math.ceil(text.length / 39)) * 19) };
}

function portsFor(node: ToulminFlowNode): ElkPort[] {
  const { data } = node;
  const ports: Array<[string, 'EAST' | 'WEST' | 'NORTH' | 'SOUTH']> = [['stack-in', 'NORTH'], ['stack-out', 'SOUTH']];
  if (data.role === 'grounds') ports.push(['out-right', 'EAST'], ['out-left', 'WEST'], ['out-bottom', 'SOUTH']);
  if (data.role === 'claim') {
    ports.push(['in-left', 'WEST']);
    for (let i = 0; i < (data.groundPortCount || 0); i++) ports.push([`in-ground-${i}`, 'WEST']);
    for (let i = 0; i < (data.groundPortCount || 0); i++) ports.push([`in-ground-outer-${i}`, 'EAST']);
    for (let i = 0; i < (data.warrantPortCount || 0); i++) ports.push([`in-warrant-${i}`, 'SOUTH']);
    for (let i = 0; i < (data.qualifierPortCount || 0); i++) ports.push([`in-qualifier-${i}`, 'WEST']);
    for (let i = 0; i < (data.rebuttalPortCount || 0); i++) ports.push([`in-rebuttal-${i}`, 'SOUTH']);
  }
  if (data.role === 'warrant') {
    ports.push(['out-top', 'NORTH'], ['out-right', 'EAST'], ['in-bottom', 'SOUTH']);
    for (let i = 0; i < (data.backingPortCount || 0); i++) ports.push([`in-backing-${i}`, 'SOUTH']);
  }
  if (data.role === 'backing') ports.push(['out-top', 'NORTH'], ['out-right', 'EAST']);
  if (data.role === 'qualifier') ports.push(['out-right', 'EAST']);
  if (data.role === 'rebuttal') ports.push(['out-top', 'NORTH'], ['out-right', 'EAST']);
  return ports.map(([id, side]) => ({ id: `${node.id}::${id}`, width: 1, height: 1, layoutOptions: { 'elk.port.side': side } }));
}

function asElkNode(node: ToulminFlowNode): ElkNode {
  const size = sizeOf(node);
  return { id: node.id, ...size, ports: portsFor(node), layoutOptions: { 'elk.portConstraints': 'FIXED_SIDE' } };
}

async function layoutStack(nodes: ToulminFlowNode[], gap = STACK_GAP): Promise<ElkNode> {
  const elk = await getElk();
  const edges: ElkExtendedEdge[] = nodes.slice(1).map((node, index) => ({
    id: `stack:${nodes[index].id}:${node.id}`,
    sources: [`${nodes[index].id}::stack-out`], targets: [`${node.id}::stack-in`],
  }));
  return elk.layout({
    id: `stack:${nodes[0]?.data.role || 'empty'}`,
    layoutOptions: {
      'elk.algorithm': 'layered', 'elk.direction': 'DOWN', 'elk.edgeRouting': 'ORTHOGONAL',
      'elk.spacing.nodeNode': String(gap), 'elk.layered.spacing.nodeNodeBetweenLayers': String(gap),
      'elk.spacing.edgeNode': '24', 'elk.spacing.edgeEdge': '14',
      'elk.layered.layering.strategy': 'LONGEST_PATH', 'elk.layered.mergeEdges': 'false',
    },
    children: nodes.map(asElkNode), edges,
  });
}

function placeStack(target: Map<string, { x: number; y: number }>, result: ElkNode, x: number, y: number) {
  for (const node of result.children || []) target.set(node.id, { x: x + (node.x || 0), y: y + (node.y || 0) });
}

function stackSize(nodes: ToulminFlowNode[], gap = STACK_GAP) {
  return { width: Math.max(0, ...nodes.map((node) => sizeOf(node).width)), height: nodes.reduce((height, node) => height + sizeOf(node).height, 0) + Math.max(0, nodes.length - 1) * gap };
}

export function evidenceColumnCount(count: number) {
  return count < 2 ? Math.max(1, count) : Math.min(3, Math.ceil(Math.sqrt(count)));
}

function placeEvidenceGrid(target: Map<string, { x: number; y: number }>, nodes: ToulminFlowNode[], x: number, y: number) {
  if (!nodes.length) return { width: 0, height: 0, columns: 0 };
  const columns = evidenceColumnCount(nodes.length);
  const rows = Math.ceil(nodes.length / columns);
  const columnWidths = Array.from({ length: columns }, (_, column) => Math.max(0, ...nodes.filter((_, index) => index % columns === column).map((node) => sizeOf(node).width)));
  const rowHeights = Array.from({ length: rows }, (_, row) => Math.max(0, ...nodes.slice(row * columns, (row + 1) * columns).map((node) => sizeOf(node).height)));
  const columnX: number[] = [];
  const rowY: number[] = [];
  columnWidths.forEach((_, index) => columnX.push((columnX[index - 1] || 0) + (index ? columnWidths[index - 1] + EVIDENCE_GAP : 0)));
  rowHeights.forEach((_, index) => rowY.push((rowY[index - 1] || 0) + (index ? rowHeights[index - 1] + STACK_GAP : 0)));
  nodes.forEach((node, index) => target.set(node.id, { x: x + columnX[index % columns], y: y + rowY[Math.floor(index / columns)] }));
  return {
    width: columnWidths.reduce((sum, width) => sum + width, 0) + Math.max(0, columns - 1) * EVIDENCE_GAP,
    height: rowHeights.reduce((sum, height) => sum + height, 0) + Math.max(0, rows - 1) * STACK_GAP,
    columns,
  };
}

export async function layoutToulminGraph(nodes: ToulminFlowNode[], edges: ToulminFlowEdge[]): Promise<ToulminLayoutResult> {
  if (!nodes.length) return { nodes, edges, width: 800, height: 420 };
  const role = (name: ToulminFlowNode['data']['role']) => nodes.filter((node) => node.data.role === name);
  const grounds = role('grounds'); const claims = role('claim'); const warrants = role('warrant');
  const backing = role('backing'); const qualifiers = role('qualifier'); const rebuttals = role('rebuttal');
  // Backing labels sit in the vertical gap between its card and the Warrant card.
  const backingGap = edges.some((edge) => edge.data.kind === 'backing' && edge.data.label)
    ? Math.max(STACK_GAP, LABEL_HEIGHT + 28) : STACK_GAP;

  // ELK computes compact stacks; this role-aware skeleton follows the reference map:
  // qualifier above the Grounds → Claim axis, with Warrant/Backing and Rebuttal beneath.
  const [claimLayout, warrantLayout, backingLayout, qualifierLayout, rebuttalLayout] = await Promise.all([
    claims.length ? layoutStack(claims) : Promise.resolve({ id: 'empty-claims', children: [] } as ElkNode),
    warrants.length ? layoutStack(warrants) : Promise.resolve({ id: 'empty-warrants', children: [] } as ElkNode),
    backing.length ? layoutStack(backing) : Promise.resolve({ id: 'empty-backing', children: [] } as ElkNode),
    qualifiers.length ? layoutStack(qualifiers) : Promise.resolve({ id: 'empty-qualifiers', children: [] } as ElkNode),
    rebuttals.length ? layoutStack(rebuttals) : Promise.resolve({ id: 'empty-rebuttals', children: [] } as ElkNode),
  ]);
  const positions = new Map<string, { x: number; y: number }>();
  const left = 32;
  const topY = 32;
  const groundSize = placeEvidenceGrid(positions, grounds, left, topY);
  const claimSize = stackSize(claims);
  const qualifierSize = stackSize(qualifiers);
  const groundX = left;
  const qualifierX = qualifiers.length ? groundX + groundSize.width + COLUMN_GAP : left;
  const claimX = claims.length
    ? qualifierX + (qualifiers.length ? qualifierSize.width + COLUMN_GAP : groundSize.width + COLUMN_GAP)
    : qualifierX + qualifierSize.width + COLUMN_GAP;

  placeStack(positions, qualifierLayout, qualifierX, topY);
  const mainRowY = qualifiers.length ? topY + qualifierSize.height + 28 : topY;
  const laidGroundSize = placeEvidenceGrid(positions, grounds, groundX, mainRowY);
  const claimY = mainRowY + Math.max(0, (laidGroundSize.height - claimSize.height) / 2);
  placeStack(positions, claimLayout, claimX, claimY);

  const mainBottom = mainRowY + Math.max(laidGroundSize.height, claimSize.height);
  // Leave a compact routing gutter between the main row and the lower Toulmin roles.
  // Support lanes use the first 18–34px of this gap; the 56px minimum keeps them clear
  // of Warrant/Rebuttal cards while avoiding the old detours around the entire graph.
  const supportCount = edges.filter((edge) => edge.data.kind === 'support' && (edge.sourceHandle === 'out-left' || edge.sourceHandle === 'out-bottom')).length;
  const supportGutter = Math.max(56, SUPPORT_LANE_START + Math.max(0, supportCount - 1) * SUPPORT_LANE_STEP + 16);
  const lowerY = mainBottom + supportGutter;
  const backingSize = stackSize(backing);
  const warrantSize = stackSize(warrants);
  const argumentCenter = claims.length
    ? (groundX + groundSize.width + claimX) / 2
    : qualifierX + qualifierSize.width / 2;
  const spineWidth = Math.max(warrantSize.width, backingSize.width);
  const spineX = Math.max(left, argumentCenter - spineWidth / 2);
  placeStack(positions, warrantLayout, spineX, lowerY);
  placeStack(positions, backingLayout, spineX, lowerY + warrantSize.height + (warrants.length && backing.length ? backingGap : 0));
  placeStack(positions, rebuttalLayout, claimX, lowerY);

  const placed = nodes.map((node) => ({ ...node, position: positions.get(node.id) || node.position }));
  const right = Math.max(...placed.map((node) => node.position.x + sizeOf(node).width));
  const bottom = Math.max(...placed.map((node) => node.position.y + sizeOf(node).height));
  return { nodes: placed, edges, width: right + 32, height: bottom + 32 };
}
