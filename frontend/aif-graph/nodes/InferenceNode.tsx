import { memo } from 'react';
import type { NodeProps } from '@xyflow/react';
import type { AifFlowNode } from '../types/aif.types';
import NodeHandles from './NodeHandles';

function InferenceNode({ id, data, selected }: NodeProps<AifFlowNode>) {
  const node = { id, data } as AifFlowNode;
  const metadata = data.originalNode.metadata || {};
  const groundedLabel = String(metadata.grounded_label || '');
  const scheme = String(metadata.scheme_id || data.text || 'Inference');
  const explanation = String(metadata.explanation || metadata.scheme_description || metadata.reconstructed_reasoning || '');
  const relationshipSummary = `${data.premiseCount || 0} premise${data.premiseCount === 1 ? '' : 's'} → ${data.conclusionCount || 0} conclusion${data.conclusionCount === 1 ? '' : 's'}`;
  const description = [explanation, relationshipSummary].filter(Boolean).join(' · ') || (scheme !== 'Inference' ? scheme : 'Inference details are available in the node panel.');
  const shortScheme = scheme.replaceAll('_', ' ');
  return <div className={`aif-flow-node aif-flow-node--inference${selected ? ' is-selected' : ''}`} aria-label={`RA inference node. ${scheme}. ${description}`} title={`${scheme}: ${description}`}>
    <NodeHandles node={node} />
    <span className="aif-flow-node__s-kind">RA</span>
    <span className="aif-flow-node__s-caption">{shortScheme}</span>
    <span className="aif-flow-node__s-summary">{relationshipSummary}</span>
    {groundedLabel ? <span className={`aif-grounded-badge is-${groundedLabel.toLowerCase()}`}>{groundedLabel}</span> : null}
  </div>;
}

export default memo(InferenceNode);
