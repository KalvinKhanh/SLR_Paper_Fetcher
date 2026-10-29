import { memo } from 'react';
import type { NodeProps } from '@xyflow/react';
import type { AifFlowNode } from '../types/aif.types';
import NodeHandles from './NodeHandles';

function InformationNode({ id, data, selected }: NodeProps<AifFlowNode>) {
  const node = { id, data } as AifFlowNode;
  const metadata = data.originalNode.metadata || {};
  const craftRole = String(metadata.craft_role || '');
  const craftLabel = String(metadata.craft_label || '');
  const roleLabel = data.informationRole === 'premise-conclusion'
    ? 'Premise · Conclusion'
    : data.informationRole;
  const craftClass = craftRole ? `is-craft-${craftRole}${metadata.craft_missing ? ' is-craft-missing' : ''}` : '';
  const status = metadata.craft_missing ? 'not identified' : data.reviewStatus;
  const statusClass = String(status).toLowerCase().replaceAll(/[^a-z0-9]+/g, '-');
  return <article className={`aif-flow-node aif-flow-node--information is-${data.informationRole} ${craftClass}${selected ? ' is-selected' : ''}`} aria-label={`${craftLabel || `Information node, ${roleLabel}`}: ${data.text}`} title={data.text}>
    <NodeHandles node={node} />
    <header className="aif-flow-node__header"><span>{craftLabel || `I · ${roleLabel}`}</span><span className={`aif-flow-node__status is-${statusClass}`}>{String(status).replaceAll('_', ' ')}</span></header>
    <div className="aif-flow-node__content">{data.text || 'No information text supplied.'}</div>
    <footer className="aif-flow-node__footer">{String(metadata.assertion_origin || 'Explicit source').replaceAll('_', ' ')}{data.groundedLabel ? <span className={`aif-grounded-badge is-${data.groundedLabel.toLowerCase()}`}> {data.groundedLabel}</span> : null}</footer>
  </article>;
}

export default memo(InformationNode);
