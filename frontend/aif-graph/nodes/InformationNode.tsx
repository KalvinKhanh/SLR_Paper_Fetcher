import { memo } from 'react';
import type { NodeProps } from '@xyflow/react';
import type { AifFlowNode } from '../types/aif.types';
import NodeHandles from './NodeHandles';

function InformationNode({ id, data, selected }: NodeProps<AifFlowNode>) {
  const node = { id, data } as AifFlowNode;
  const roleLabel = data.informationRole === 'premise-conclusion'
    ? 'Premise · Conclusion'
    : data.informationRole;
  return <article className={`aif-flow-node aif-flow-node--information is-${data.informationRole}${selected ? ' is-selected' : ''}`} aria-label={`Information node, ${roleLabel}: ${data.text}`} title={data.text}>
    <NodeHandles node={node} />
    <header className="aif-flow-node__header"><span>I · {roleLabel}</span><span className={`aif-flow-node__status is-${data.reviewStatus.toLowerCase().replaceAll('_', '-')}`}>{data.reviewStatus.replaceAll('_', ' ')}</span></header>
    <div className="aif-flow-node__content">{data.text || 'No information text supplied.'}</div>
    <footer className="aif-flow-node__footer">{String(data.originalNode.metadata?.assertion_origin || 'Explicit source').replaceAll('_', ' ')}{data.groundedLabel ? <span className={`aif-grounded-badge is-${data.groundedLabel.toLowerCase()}`}> {data.groundedLabel}</span> : null}</footer>
  </article>;
}

export default memo(InformationNode);
