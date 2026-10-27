import { memo } from 'react';
import type { NodeProps } from '@xyflow/react';
import type { AifFlowNode } from '../types/aif.types';
import NodeHandles from './NodeHandles';

function ConflictNode({ id, data, selected }: NodeProps<AifFlowNode>) {
  const node = { id, data } as AifFlowNode;
  return <div className={`aif-flow-node aif-flow-node--diamond aif-flow-node--conflict${selected ? ' is-selected' : ''}`} aria-label={`CA conflict node. ${data.text}`} title={data.text}>
    <NodeHandles node={node} />
    <span className="aif-flow-node__s-kind">CA</span>
  </div>;
}

export default memo(ConflictNode);
