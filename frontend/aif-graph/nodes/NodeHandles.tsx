import { memo, useLayoutEffect } from 'react';
import { Handle, Position, useUpdateNodeInternals } from '@xyflow/react';
import type { AifFlowNode } from '../types/aif.types';

function NodeHandles({ node }: { node: AifFlowNode }) {
  const updateNodeInternals = useUpdateNodeInternals();
  useLayoutEffect(() => updateNodeInternals(node.id), [node.id, node.data.sourceHandles.length, node.data.targetHandles.length, node.data.portOffsets, updateNodeInternals]);
  const handle = (id: string, side: Position, fallback: number) => (
    <Handle
      key={id}
      id={id}
      type={id.startsWith('out-') ? 'source' : 'target'}
      position={side}
      isConnectable={false}
      style={{ top: `${(node.data.portOffsets?.[id] ?? fallback) * 100}%` }}
    />
  );
  return <>
    {node.data.sourceHandles.map((item) => handle(item.id, item.side, item.offset))}
    {node.data.targetHandles.map((item) => handle(item.id, item.side, item.offset))}
  </>;
}

export default memo(NodeHandles);
