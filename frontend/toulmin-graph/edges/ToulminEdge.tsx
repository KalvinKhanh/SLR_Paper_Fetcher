import { memo } from 'react';
import { BaseEdge, EdgeLabelRenderer, Position, useReactFlow, type EdgeProps } from '@xyflow/react';
import type { ToulminFlowEdge, ToulminFlowNode } from '../types/toulmin.types';
import { SUPPORT_LANE_START, SUPPORT_LANE_STEP } from '../layout/elkLayout';

type Point = { x: number; y: number };
type Box = { x: number; y: number; width: number; height: number };

function edgeColor(kind?: string) {
  return kind === 'warrant' ? '#8a5b00'
    : kind === 'backing' ? '#6b46a1'
      : kind === 'qualifier' ? '#2463a8'
        : kind === 'rebuttal' ? '#b33636'
          : '#256b83';
}

function pathIsClear(points: Point[], nodes: ToulminFlowNode[], sourceId: string, targetId: string) {
  const obstacles = nodes.filter((node) => node.id !== sourceId && node.id !== targetId).map((node) => {
    const box = nodeBox(node);
    return { x: box.x - 8, y: box.y - 8, width: box.width + 16, height: box.height + 16 };
  });
  for (let index = 1; index < points.length; index++) {
    const start = points[index - 1]; const end = points[index];
    if (Math.abs(start.y - end.y) < 1 && obstacles.some((box) => start.y > box.y && start.y < box.y + box.height && Math.max(start.x, end.x) > box.x && Math.min(start.x, end.x) < box.x + box.width)) return false;
    if (Math.abs(start.x - end.x) < 1 && obstacles.some((box) => start.x > box.x && start.x < box.x + box.width && Math.max(start.y, end.y) > box.y && Math.min(start.y, end.y) < box.y + box.height)) return false;
  }
  return true;
}

function pathLength(points: Point[]) {
  return points.slice(1).reduce((length, point, index) => length + Math.abs(point.x - points[index].x) + Math.abs(point.y - points[index].y), 0);
}

function orthogonalPath(source: Point, target: Point, sourcePosition: Position, targetPosition: Position, kind: string | undefined, index: number, nodes: ToulminFlowNode[], sourceId: string, targetId: string): Point[] {
  const dx = target.x - source.x;
  const dy = target.y - source.y;
  const near = 16 + (index % 4) * 5;
  const isHorizontalPair = (sourcePosition === Position.Right || sourcePosition === Position.Left)
    && (targetPosition === Position.Left || targetPosition === Position.Right);
  const isVerticalPair = (sourcePosition === Position.Top || sourcePosition === Position.Bottom)
    && (targetPosition === Position.Top || targetPosition === Position.Bottom);

  // Straight segments are clearer for aligned nodes, particularly Backing → Warrant.
  if (isHorizontalPair && Math.abs(dy) < 3 && pathIsClear([source, target], nodes, sourceId, targetId)) return [source, target];
  if (isVerticalPair && Math.abs(dx) < 3 && pathIsClear([source, target], nodes, sourceId, targetId)) return [source, target];

  if (kind === 'support' && sourcePosition === Position.Left) {
    const grounds = nodes.filter((node) => node.data.role === 'grounds');
    const leftLane = Math.min(...grounds.map((node) => node.position.x)) - 24 - (index % 4) * 10;
    const mainBottom = Math.max(...nodes.filter((node) => ['grounds', 'claim', 'qualifier'].includes(node.data.role)).map((node) => node.position.y + nodeBox(node).height));
    const lowerLane = mainBottom + SUPPORT_LANE_START + index * SUPPORT_LANE_STEP;
    const rightLane = Math.max(...nodes.map((node) => node.position.x + nodeBox(node).width)) + 16 + index * 14;
    const route = [source, { x: leftLane, y: source.y }, { x: leftLane, y: lowerLane }, { x: rightLane, y: lowerLane }, { x: rightLane, y: target.y }, target];
    if (pathIsClear(route, nodes, sourceId, targetId)) return route;
  }

  if (kind === 'support' && sourcePosition === Position.Bottom) {
    const sourceNode = nodes.find((node) => node.id === sourceId);
    if (sourceNode) {
      const grounds = nodes.filter((node) => node.data.role === 'grounds');
      const groundsRight = Math.max(...grounds.map((node) => node.position.x + nodeBox(node).width));
      const mainBottom = Math.max(...nodes.filter((node) => ['grounds', 'claim', 'qualifier'].includes(node.data.role)).map((node) => node.position.y + nodeBox(node).height));
      const sourceRowBottom = Math.max(...grounds.filter((node) => Math.abs(node.position.y - sourceNode.position.y) < 4).map((node) => node.position.y + nodeBox(node).height));
      const rowGapY = sourceRowBottom + 12;
      const lowerLane = mainBottom + SUPPORT_LANE_START + index * SUPPORT_LANE_STEP;
      const corridorX = groundsRight + 16 + index * 14;
      const rightLane = Math.max(...nodes.map((node) => node.position.x + nodeBox(node).width)) + 16 + index * 14;
      const route = [source, { x: source.x, y: rowGapY }, { x: corridorX, y: rowGapY }, { x: corridorX, y: lowerLane }, { x: rightLane, y: lowerLane }, { x: rightLane, y: target.y }, target];
      if (pathIsClear(route, nodes, sourceId, targetId)) return route;
      // If nodes have been manually dragged into the row gap, route below the complete
      // graph and return from outside its right boundary instead of selecting a blocked lane.
      const bottomLane = Math.max(...nodes.map((node) => node.position.y + nodeBox(node).height)) + 24 + index * 8;
      const outsideRoute = [source, { x: source.x, y: rowGapY }, { x: corridorX, y: rowGapY }, { x: corridorX, y: bottomLane }, { x: rightLane, y: bottomLane }, { x: rightLane, y: target.y }, target];
      return outsideRoute;
    }
  }

  if (kind === 'support' || kind === 'qualifier') {
    // Fan parallel Grounds routes across separate vertical lanes instead of stacking them.
    const ratio = kind === 'support' ? Math.min(0.38, 0.18 + index * 0.055) : Math.min(0.68, 0.42 + (index % 4) * 0.065);
    const candidates = Array.from({ length: 5 }, (_, lane) => {
      const laneX = source.x + dx * Math.min(0.72, ratio + lane * 0.045);
      return [source, { x: laneX, y: source.y }, { x: laneX, y: target.y }, target];
    });
    const clear = candidates.filter((candidate) => pathIsClear(candidate, nodes, sourceId, targetId));
    return (clear.length ? clear : candidates).sort((a, b) => pathLength(a) - pathLength(b))[0];
  }

  if (kind === 'warrant' || kind === 'backing' || kind === 'rebuttal') {
    if (sourcePosition === Position.Right) {
      // Later nodes in a vertical stack leave via the outside lane so their path cannot
      // pass through the sibling cards between them and the Claim/Warrant.
      const laneX = Math.max(source.x, target.x) + 24 + (index % 4) * 10;
      const approachY = target.y + 20 + (index % 3) * 6;
      const route = [source, { x: laneX, y: source.y }, { x: laneX, y: approachY }, { x: target.x, y: approachY }, target];
      if (pathIsClear(route, nodes, sourceId, targetId)) return route;
      const upperLane = Math.min(...nodes.map((node) => node.position.y)) - 24 - (index % 4) * 12;
      const outsideX = Math.max(...nodes.map((node) => node.position.x + nodeBox(node).width)) + 24 + (index % 4) * 12;
      return [source, { x: outsideX, y: source.y }, { x: outsideX, y: upperLane }, { x: target.x, y: upperLane }, target];
    }
    // Rebuttal and Warrant use distinct horizontal lanes before entering their Claim ports.
    const ratio = kind === 'warrant' ? 0.38 : kind === 'rebuttal' ? Math.min(0.82, 0.72 + (index % 4) * 0.035) : 0.5;
    const candidates = [0, 1, 2, 3].map((lane) => {
      const offsetRatio = lane === 0 ? ratio : Math.max(0.12, Math.min(0.9, ratio + (lane % 2 ? 1 : -1) * Math.ceil(lane / 2) * 0.11));
      const y = source.y + (target.y - source.y) * offsetRatio;
      return [source, { x: source.x, y }, { x: target.x, y }, target];
    });
    const clear = candidates.filter((candidate) => pathIsClear(candidate, nodes, sourceId, targetId));
    return (clear.length ? clear : candidates).sort((a, b) => pathLength(a) - pathLength(b))[0];
  }

  if (isHorizontalPair) {
    const laneX = target.x - Math.sign(dx || 1) * near;
    return [source, { x: laneX, y: source.y }, { x: laneX, y: target.y }, target];
  }
  const laneY = target.y - Math.sign(dy || 1) * near;
  return [source, { x: source.x, y: laneY }, { x: target.x, y: laneY }, target];
}

function pathFor(points: Point[]) {
  return points.reduce((path, point, index) => `${path}${index ? ' L' : 'M'} ${point.x} ${point.y}`, '');
}

function labelSize(text: string): { width: number; height: number } {
  if (typeof document !== 'undefined') {
    const canvas = document.createElement('canvas');
    const context = canvas.getContext('2d');
    if (context) {
      context.font = '500 11px Inter, system-ui, sans-serif';
      return { width: Math.ceil(context.measureText(text).width + 22), height: 24 };
    }
  }
  return { width: Math.ceil(text.length * 6.2 + 22), height: 24 };
}

function intersects(a: Box, b: Box) {
  return a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;
}

function nodeBox(node: ToulminFlowNode): Box {
  const measured = (node as ToulminFlowNode & { measured?: { width?: number; height?: number } }).measured;
  return {
    x: node.position.x, y: node.position.y,
    width: measured?.width || Number(node.style?.width) || 284,
    height: measured?.height || Number(node.style?.height) || Math.max(112, 74 + Math.ceil((node.data.text || '').length / 39) * 19),
  };
}

function findLabelPoint(points: Point[], text: string, nodes: ToulminFlowNode[]): Point | null {
  const size = labelSize(text);
  const nodeBoxes = nodes.map(nodeBox).map((box) => ({ x: box.x - 12, y: box.y - 12, width: box.width + 24, height: box.height + 24 }));
  const segments = points.slice(1).map((end, index) => {
    const start = points[index];
    const horizontal = Math.abs(start.y - end.y) < 1;
    return { start, end, horizontal, length: Math.hypot(end.x - start.x, end.y - start.y) };
  }).sort((a, b) => Number(b.horizontal) - Number(a.horizontal) || b.length - a.length);

  for (const segment of segments) {
    // Keep the pill clear of the stroke itself; a label centered on the line looks cramped
    // and can become hard to read at lower zoom levels.
    const offsets = segment.horizontal ? [-size.height / 2 - 12, size.height / 2 + 12, -size.height / 2 - 26, size.height / 2 + 26]
      : [size.width / 2 + 12, -size.width / 2 - 12, size.width / 2 + 26, -size.width / 2 - 26];
    for (const offset of offsets) {
      const midpoint = { x: (segment.start.x + segment.end.x) / 2, y: (segment.start.y + segment.end.y) / 2 };
      const point = segment.horizontal ? { x: midpoint.x, y: midpoint.y + offset } : { x: midpoint.x + offset, y: midpoint.y };
      // Keep labels away from both endpoints so they never cover node borders or arrowheads.
      const requiredLength = segment.horizontal ? size.width + 32 : size.height + 32;
      const usableEnough = segment.length >= requiredLength || Math.abs(offset) > 0;
      const labelBox = { x: point.x - size.width / 2, y: point.y - size.height / 2, ...size };
      if (usableEnough && !nodeBoxes.some((box) => intersects(labelBox, box))) return point;
    }
  }
  return null;
}

function ToulminEdge(props: EdgeProps<ToulminFlowEdge>) {
  const { id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, markerEnd, data, selected } = props;
  const { getNodes } = useReactFlow<ToulminFlowNode, ToulminFlowEdge>();
  // Use live handle coordinates: drag, zoom and auto layout all regenerate the same valid route.
  const color = edgeColor(data?.kind);
  const allNodes = getNodes() as ToulminFlowNode[];
  const points = orthogonalPath({ x: sourceX, y: sourceY }, { x: targetX, y: targetY }, sourcePosition, targetPosition, data?.kind, data?.routingIndex || 0, allNodes, props.source, props.target);
  const label = data?.label ? findLabelPoint(points, data.label, allNodes) : null;
  const dashed = data?.kind === 'qualifier' || data?.kind === 'rebuttal';
  return <>
    <BaseEdge id={id} path={pathFor(points)} markerEnd={markerEnd} style={{ stroke: color, strokeWidth: selected ? 2.2 : 1.8, strokeDasharray: dashed ? '5 4' : undefined, opacity: data?.isMuted ? 0.15 : 1 }} interactionWidth={18} />
    {data?.label && label ? <EdgeLabelRenderer>
      <span className={`toulmin-edge-label${selected ? ' is-selected' : ''}`} style={{ transform: `translate(-50%, -50%) translate(${label.x}px,${label.y}px)`, color, opacity: data?.isMuted ? 0.15 : 1 }}>{data.label}</span>
    </EdgeLabelRenderer> : null}
  </>;
}

export default memo(ToulminEdge);
