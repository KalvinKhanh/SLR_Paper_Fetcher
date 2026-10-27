import { memo } from 'react';
import type { NodeProps } from '@xyflow/react';
import type { ToulminFlowNode } from '../types/toulmin.types';
import ArgumentRoleNode from './ArgumentRoleNode';

function WarrantNode(props: NodeProps<ToulminFlowNode>) { return <ArgumentRoleNode {...props} role="warrant" />; }
export default memo(WarrantNode);
