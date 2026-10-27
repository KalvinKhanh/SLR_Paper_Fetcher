import { memo } from 'react';
import type { NodeProps } from '@xyflow/react';
import type { ToulminFlowNode } from '../types/toulmin.types';
import ArgumentRoleNode from './ArgumentRoleNode';

function GroundsNode(props: NodeProps<ToulminFlowNode>) { return <ArgumentRoleNode {...props} role="grounds" />; }
export default memo(GroundsNode);
