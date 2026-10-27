import { memo } from 'react';
import type { NodeProps } from '@xyflow/react';
import type { AifFlowNode } from '../types/aif.types';
import NodeHandles from './NodeHandles';

function PreferenceNode({ id, data, selected }: NodeProps<AifFlowNode>) {
  const node = { id, data } as AifFlowNode;
  return <div className={`aif-flow-node aif-flow-node--diamond aif-flow-node--preference${selected ? ' is-selected' : ''}`} aria-label={`PA preference node. ${data.text}`} title={data.text}>
    <NodeHandles node={node} />
    <span className="aif-flow-node__s-kind">PA</span>
  </div>;
}

export default memo(PreferenceNode);
