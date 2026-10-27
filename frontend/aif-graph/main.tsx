import { createRoot, type Root } from 'react-dom/client';
import { AIFGraph } from './AIFGraph';
import type { AifGraphPayload } from './types/aif.types';
import '@xyflow/react/dist/style.css';
import './aif-graph.css';

const container = document.getElementById('aifReactFlowRoot');
let root: Root | null = null;
if (container) root = createRoot(container);

window.renderAifReactFlow = (payload: AifGraphPayload) => {
  if (!root) return;
  root.render(<AIFGraph payload={payload} />);
};

if (window.pendingAifReactFlow) {
  window.renderAifReactFlow(window.pendingAifReactFlow);
  delete window.pendingAifReactFlow;
}
