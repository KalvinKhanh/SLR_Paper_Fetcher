import { memo } from 'react';
import { Panel } from '@xyflow/react';
import type { ToulminFlowNode } from './types/toulmin.types';

interface Props {
  nodes: ToulminFlowNode[];
  activeNodeId: string | null;
  onAutoLayout: () => void;
  onFitView: () => void;
  onSelectNode: (node: ToulminFlowNode) => void;
}

function ToulminGraphToolbar({ nodes, activeNodeId, onAutoLayout, onFitView, onSelectNode }: Props) {
  const findings = nodes.filter((node) => node.data.role === 'grounds');
  const claims = nodes.filter((node) => node.data.role === 'claim');
  return (
    <>
      <Panel position="top-left" className="toulmin-flow-navigation" aria-label="Navigate argument statements">
        {findings.map((node, index) => (
          <button key={node.id} type="button" aria-pressed={activeNodeId === node.id} title={node.data.text} onClick={() => onSelectNode(node)}>
            <span className="toulmin-flow-navigation__index">F{index + 1}</span>
            <span>Finding {index + 1}</span>
          </button>
        ))}
        {claims.map((node, index) => (
          <button key={node.id} type="button" aria-pressed={activeNodeId === node.id} title={node.data.text} onClick={() => onSelectNode(node)}>
            <span className="toulmin-flow-navigation__index toulmin-flow-navigation__index--claim">C</span>
            <span>{claims.length > 1 ? `Conclusion ${index + 1}` : 'Conclusion'}</span>
          </button>
        ))}
      </Panel>
      <Panel position="top-right" className="toulmin-flow-toolbar">
        <button type="button" onClick={onAutoLayout} title="Recalculate automatic layout">Auto layout</button>
        <button type="button" onClick={onFitView} title="Center the complete argument graph">Center graph</button>
      </Panel>
    </>
  );
}

export default memo(ToulminGraphToolbar);
