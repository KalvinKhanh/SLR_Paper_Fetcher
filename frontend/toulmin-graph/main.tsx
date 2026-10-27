import { createRoot, type Root } from 'react-dom/client';
import { ToulminGraph } from './ToulminGraph';
import type { ToulminGraphPayload } from './types/toulmin.types';
import '@xyflow/react/dist/style.css';
import './toulmin-graph.css';

const container = document.getElementById('toulminGraphRoot');
let root: Root | null = null;
if (container) root = createRoot(container);

window.renderToulminReactFlow = (payload: ToulminGraphPayload) => {
  if (!root) return;
  root.render(<ToulminGraph payload={payload} />);
};

if (window.pendingToulminReactFlow) {
  window.renderToulminReactFlow(window.pendingToulminReactFlow);
  delete window.pendingToulminReactFlow;
}
