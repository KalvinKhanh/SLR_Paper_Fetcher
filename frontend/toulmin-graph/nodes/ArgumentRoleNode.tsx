import { memo, useLayoutEffect } from 'react';
import { Handle, Position, useUpdateNodeInternals, type NodeProps } from '@xyflow/react';
import type { ToulminFlowNode, ToulminNodeData, ToulminNodeType } from '../types/toulmin.types';

const handleStyle = { opacity: 0, width: 7, height: 7, background: 'currentColor', border: 0 };
type RoleNodeProps = NodeProps<ToulminFlowNode> & { role: ToulminNodeType };

function AllSideHandles({ role, data }: { role: ToulminNodeType; data: ToulminNodeData }) {
  const handles: Array<{ id: string; type: 'source' | 'target'; position: Position; style?: React.CSSProperties }> = [];
  if (role === 'grounds') {
    handles.push(
      { id: 'out-right', type: 'source', position: Position.Right },
      { id: 'out-left', type: 'source', position: Position.Left },
      { id: 'out-bottom', type: 'source', position: Position.Bottom },
    );
  } else if (role === 'claim') {
    for (let index = 0; index < (data.groundPortCount || 0); index++) {
      handles.push({ id: `in-ground-${index}`, type: 'target', position: Position.Left, style: { top: `${((index + 1) / ((data.groundPortCount || 0) + 1)) * 55}%` } });
      // Inner Grounds route around the lower roles and enter at distinct right-side ports.
      handles.push({ id: `in-ground-outer-${index}`, type: 'target', position: Position.Right, style: { top: `${20 + ((index + 1) / ((data.groundPortCount || 0) + 1)) * 60}%` } });
    }
    handles.push({ id: 'in-left', type: 'target', position: Position.Left });
    for (let index = 0; index < (data.rebuttalPortCount || 0); index++) handles.push({ id: `in-rebuttal-${index}`, type: 'target', position: Position.Bottom, style: { left: `${(0.68 + ((index + 1) / ((data.rebuttalPortCount || 0) + 1)) * 0.22) * 100}%` } });
    for (let index = 0; index < (data.warrantPortCount || 0); index++) handles.push({ id: `in-warrant-${index}`, type: 'target', position: Position.Bottom, style: { left: `${(0.12 + ((index + 1) / ((data.warrantPortCount || 0) + 1)) * 0.28) * 100}%` } });
    for (let index = 0; index < (data.qualifierPortCount || 0); index++) handles.push({ id: `in-qualifier-${index}`, type: 'target', position: Position.Left, style: { top: `${58 + ((index + 1) / ((data.qualifierPortCount || 0) + 1)) * 22}%` } });
  } else if (role === 'warrant') {
    handles.push({ id: 'out-top', type: 'source', position: Position.Top }, { id: 'out-right', type: 'source', position: Position.Right }, { id: 'in-bottom', type: 'target', position: Position.Bottom });
    for (let index = 0; index < (data.backingPortCount || 0); index++) handles.push({ id: `in-backing-${index}`, type: 'target', position: Position.Bottom, style: { left: `${(0.1 + ((index + 1) / ((data.backingPortCount || 0) + 1)) * 0.8) * 100}%` } });
  } else if (role === 'backing') {
    handles.push({ id: 'out-top', type: 'source', position: Position.Top }, { id: 'out-right', type: 'source', position: Position.Right }, { id: 'in-bottom', type: 'target', position: Position.Bottom });
  } else if (role === 'qualifier') {
    handles.push({ id: 'out-right', type: 'source', position: Position.Right });
  } else if (role === 'rebuttal') {
    handles.push({ id: 'out-top', type: 'source', position: Position.Top }, { id: 'out-right', type: 'source', position: Position.Right });
  }
  return <>{handles.map((handle) => <Handle key={handle.id} id={handle.id} type={handle.type} position={handle.position} isConnectable={false} style={{ ...handleStyle, ...handle.style }} />)}</>;
}

function ArgumentRoleNode({ id, data, selected, role }: RoleNodeProps) {
  const updateNodeInternals = useUpdateNodeInternals();
  useLayoutEffect(() => {
    updateNodeInternals(id);
  }, [id, updateNodeInternals, data.groundPortCount, data.warrantPortCount, data.backingPortCount, data.qualifierPortCount, data.rebuttalPortCount]);
  return (
    <article className={`toulmin-node toulmin-node--${role}${selected ? ' is-selected' : ''}`} aria-label={`${data.label}: ${data.text}`}>
      <AllSideHandles role={role} data={data} />
      <header className="toulmin-node__header">
        <span className="toulmin-node__title">{data.label}</span>
        <span className={`toulmin-node__status toulmin-node__status--${data.reviewStatus.toLowerCase().replaceAll('_', '-')}`}>{data.reviewStatus.replaceAll('_', ' ')}</span>
      </header>
      <p className="toulmin-node__content">{data.text}</p>
      <footer className="toulmin-node__footer">
        <span>{data.origin === 'inferred' ? 'AI reconstructed' : data.origin === 'hypothetical' ? 'Hypothetical' : 'Explicit source'}</span>
        {data.sourceReference?.page ? <span>Page {String(data.sourceReference.page)}</span> : null}
      </footer>
    </article>
  );
}

export default memo(ArgumentRoleNode);
