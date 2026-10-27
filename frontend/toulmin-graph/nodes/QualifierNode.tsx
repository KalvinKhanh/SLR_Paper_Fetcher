import { memo } from 'react';
import type { NodeProps } from '@xyflow/react';
import type { ToulminFlowNode } from '../types/toulmin.types';
import ArgumentRoleNode from './ArgumentRoleNode';

function QualifierNode(props: NodeProps<ToulminFlowNode>) { return <ArgumentRoleNode {...props} role="qualifier" />; }
export default memo(QualifierNode);
