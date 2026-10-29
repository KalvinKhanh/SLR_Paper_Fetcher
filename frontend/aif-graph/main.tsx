import { createRoot, type Root } from 'react-dom/client';
import { AIFGraph } from './AIFGraph';
import type { AifGraphPayload } from './types/aif.types';
import '@xyflow/react/dist/style.css';
import './aif-graph.css';

const roots = new Map<string, Root>();
for (const id of ['aifReactFlowRoot', 'craftReactFlowRoot']) {
  const container = document.getElementById(id);
  if (container) roots.set(id, createRoot(container));
}

window.renderAifReactFlow = (payload: AifGraphPayload) => {
  roots.get('aifReactFlowRoot')?.render(<AIFGraph payload={{ ...payload, view: 'aif' }} />);
};

window.renderCraftReactFlow = (payload: AifGraphPayload) => {
  roots.get('craftReactFlowRoot')?.render(<AIFGraph payload={{ ...payload, view: 'craft' }} />);
};

if (window.pendingAifReactFlow) {
  window.renderAifReactFlow(window.pendingAifReactFlow);
  delete window.pendingAifReactFlow;
}

if (window.pendingCraftReactFlow) {
  window.renderCraftReactFlow(window.pendingCraftReactFlow);
  delete window.pendingCraftReactFlow;
}
