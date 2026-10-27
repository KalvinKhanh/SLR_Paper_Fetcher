import { memo } from 'react';
import { BaseEdge, Position, useReactFlow, type EdgeProps } from '@xyflow/react';
import type { AifFlowEdge, AifFlowNode } from '../types/aif.types';

type Point = { x: number; y: number };

function intersectsPath(points: Point[], nodes: AifFlowNode[], sourceId: string, targetId: string) {
  const obstacles = nodes.filter((node) => node.id !== sourceId && node.id !== targetId).map((node) => {
    const measured = node.measured;
    return { x: node.position.x - 10, y: node.position.y - 10, width: measured?.width || node.data.width, height: measured?.height || Number(node.style?.height) || 92 };
  });
  for (let index = 1; index < points.length; index++) {
    const first = points[index - 1]; const second = points[index];
    if (Math.abs(first.y - second.y) < 1) {
      const left = Math.min(first.x, second.x); const right = Math.max(first.x, second.x);
      if (obstacles.some((box) => first.y > box.y && first.y < box.y + box.height && right > box.x && left < box.x + box.width)) return true;
    } else {
      const top = Math.min(first.y, second.y); const bottom = Math.max(first.y, second.y);
      if (obstacles.some((box) => first.x > box.x && first.x < box.x + box.width && bottom > box.y && top < box.y + box.height)) return true;
    }
  }
  return false;
}

function pathLength(points: Point[]) { return points.slice(1).reduce((total, point, index) => total + Math.hypot(point.x - points[index].x, point.y - points[index].y), 0); }

function orthogonal(source: Point, target: Point, sourcePosition: Position, targetPosition: Position, nodes: AifFlowNode[], sourceId: string, targetId: string): Point[] {
  const aligned = sourcePosition === Position.Right && targetPosition === Position.Left && Math.abs(source.y - target.y) < 2
    || sourcePosition === Position.Left && targetPosition === Position.Right && Math.abs(source.y - target.y) < 2;
  const straight = [source, target];
  if (aligned && !intersectsPath(straight, nodes, sourceId, targetId)) return straight;
  const horizontal = sourcePosition === Position.Left || sourcePosition === Position.Right;
  const mid = horizontal ? (source.x + target.x) / 2 : (source.y + target.y) / 2;
  const candidates: Point[][] = horizontal
    ? [mid, source.x + 24, target.x - 24].map((laneX) => [source, { x: laneX, y: source.y }, { x: laneX, y: target.y }, target])
    : [[source, { x: source.x, y: mid }, { x: target.x, y: mid }, target]];
  const obstacles = nodes.filter((node) => node.id !== sourceId && node.id !== targetId);
  if (horizontal) {
    const topLane = Math.min(source.y, target.y, ...obstacles.map((node) => node.position.y)) - 24;
    const bottomLane = Math.max(source.y, target.y, ...obstacles.map((node) => node.position.y + (node.measured?.height || Number(node.style?.height) || 92))) + 24;
    for (const laneY of [topLane, bottomLane]) {
      const approach = target.x >= source.x ? target.x - 24 : target.x - 24;
      candidates.push([source, { x: source.x + 24, y: source.y }, { x: source.x + 24, y: laneY }, { x: approach, y: laneY }, { x: approach, y: target.y }, target]);
    }
  }
  const clear = candidates.filter((candidate) => !intersectsPath(candidate, nodes, sourceId, targetId));
  return (clear.length ? clear : candidates).sort((a, b) => pathLength(a) - pathLength(b))[0];
}

function pathFor(points: Point[]) {
  return points.reduce((path, point, index) => `${path}${index ? ' L' : 'M'} ${point.x} ${point.y}`, '');
}

function AifEdge(props: EdgeProps<AifFlowEdge>) {
  const { id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, markerEnd, selected, data } = props;
  const { getNodes } = useReactFlow<AifFlowNode, AifFlowEdge>();
  const route = data?.routePoints;
  const current = [{ x: sourceX, y: sourceY }, { x: targetX, y: targetY }];
  const useElkRoute = route && route.length >= 2 && Math.hypot(route[0].x - current[0].x, route[0].y - current[0].y) < 5 && Math.hypot(route.at(-1)!.x - current[1].x, route.at(-1)!.y - current[1].y) < 5;
  const nodes = getNodes() as AifFlowNode[];
  const points = useElkRoute ? route : orthogonal(current[0], current[1], sourcePosition, targetPosition, nodes, props.source, props.target);
  const color = data?.relationshipKind === 'conflict' ? '#b84c56' : data?.relationshipKind === 'preference' ? '#7751a8' : '#8793a2';
  const related = nodes.some((node) => node.selected && (node.id === props.source || node.id === props.target));
  const highlighted = selected || related || data?.isHighlighted;
  return <BaseEdge id={id} path={pathFor(points)} markerEnd={markerEnd} className={highlighted ? 'is-highlighted' : undefined} style={{ stroke: highlighted ? '#2563eb' : color, strokeWidth: selected ? 2.5 : highlighted ? 2.2 : 1.6, opacity: data?.isMuted || (nodes.some((node) => node.selected) && !related) ? 0.2 : 1 }} interactionWidth={18} />;
}

export default memo(AifEdge);
