/**
 * ExecutionOrderBadge
 *
 * Renders a small numbered badge in the top-left corner of a workflow node,
 * showing its execution order (from topological sort).
 *
 * Usage: Add <ExecutionOrderBadge order={data._executionOrder} /> at the top
 * of any node's render tree, inside the outermost container div.
 */

export const ExecutionOrderBadge = ({ order }: { order?: number }) => {
  if (order === undefined) return null;

  return (
    <div
      className="absolute -top-2.5 -left-2.5 z-20 flex items-center justify-center w-6 h-6 rounded-full text-[10px] font-black leading-none select-none pointer-events-none"
      style={{
        background: 'linear-gradient(135deg, hsl(263 70% 55%), hsl(263 70% 40%))',
        color: '#fff',
        boxShadow: '0 2px 6px rgba(0,0,0,0.4)',
        border: '2px solid #1a1a2e',
      }}
    >
      {order}
    </div>
  );
};
