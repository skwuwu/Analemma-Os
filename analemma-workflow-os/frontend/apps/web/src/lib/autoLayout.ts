/**
 * Auto-Layout Engine
 *
 * Dagre-based hierarchical (Sugiyama) layout for workflow graphs.
 * Computes optimal top-to-bottom node positions with configurable spacing.
 */

import dagre from '@dagrejs/dagre';
import type { Node, Edge } from '@xyflow/react';

// Default node dimensions used by dagre for spacing calculations
const DEFAULT_NODE_WIDTH = 240;
const DEFAULT_NODE_HEIGHT = 80;

export interface LayoutOptions {
  /** Layout direction: TB (top-bottom), LR (left-right) */
  direction?: 'TB' | 'LR';
  /** Horizontal gap between nodes */
  nodeSpacingX?: number;
  /** Vertical gap between nodes */
  nodeSpacingY?: number;
}

/**
 * Compute a hierarchical auto-layout using dagre (Sugiyama algorithm).
 * Returns new nodes array with updated positions; edges are unchanged.
 */
export function computeAutoLayout(
  nodes: Node[],
  edges: Edge[],
  options: LayoutOptions = {}
): Node[] {
  const {
    direction = 'TB',
    nodeSpacingX = 80,
    nodeSpacingY = 100,
  } = options;

  if (nodes.length === 0) return nodes;

  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({
    rankdir: direction,
    nodesep: nodeSpacingX,
    ranksep: nodeSpacingY,
    marginx: 40,
    marginy: 40,
  });

  // Add nodes
  for (const node of nodes) {
    const w = node.measured?.width ?? DEFAULT_NODE_WIDTH;
    const h = node.measured?.height ?? DEFAULT_NODE_HEIGHT;
    g.setNode(node.id, { width: w, height: h });
  }

  // Add edges (skip back-edges to avoid layout cycles)
  for (const edge of edges) {
    if (edge.data?.isBackEdge) continue;
    if (g.hasNode(edge.source) && g.hasNode(edge.target)) {
      g.setEdge(edge.source, edge.target);
    }
  }

  dagre.layout(g);

  return nodes.map((node) => {
    const dagreNode = g.node(node.id);
    if (!dagreNode) return node;

    const w = node.measured?.width ?? DEFAULT_NODE_WIDTH;
    const h = node.measured?.height ?? DEFAULT_NODE_HEIGHT;

    return {
      ...node,
      position: {
        x: dagreNode.x - w / 2,
        y: dagreNode.y - h / 2,
      },
    };
  });
}
