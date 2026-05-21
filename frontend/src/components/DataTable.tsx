import React from 'react';
import EmptyState from './EmptyState';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export interface Column<T = any> {
  header: string;
  accessor: string;
  render?: (row: T) => React.ReactNode;
}

interface DataTableProps<T> {
  columns: Column<T>[];
  data: T[];
  onRowClick?: (row: T) => void;
  emptyTitle?: string;
  emptyDescription?: string;
  /** Stable key per row — avoids array-index reconciliation bugs on delete/reorder. */
  getRowKey?: (row: T, index: number) => string | number;
}

function DataTable<T>({
  columns,
  data,
  onRowClick,
  emptyTitle = 'No data found',
  emptyDescription = 'There are no records to display.',
  getRowKey,
}: DataTableProps<T>) {
  if (data.length === 0) {
    return (
      <div className="card">
        <EmptyState title={emptyTitle} description={emptyDescription} />
      </div>
    );
  }

  return (
    <div className="card overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-token bg-surface-2">
              {columns.map((col, ci) => (
                <th
                  key={`${col.accessor}-${ci}`}
                  className="px-4 py-3 text-left text-[11px] font-semibold uppercase tracking-wider text-muted"
                >
                  {col.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.map((row, i) => {
              const rowKey = getRowKey
                ? getRowKey(row, i)
                : ((row as Record<string, unknown>).id as string | number) ?? i;
              return (
                <tr
                  key={rowKey}
                  onClick={() => onRowClick?.(row)}
                  className={`border-b border-[color:var(--color-border)] transition-colors last:border-0 hover:bg-surface-2 ${
                    onRowClick ? 'cursor-pointer' : ''
                  }`}
                >
                  {columns.map((col, ci) => (
                    <td
                      key={`${col.accessor}-${ci}`}
                      className="whitespace-nowrap px-4 py-3 text-secondary"
                    >
                      {col.render
                        ? col.render(row)
                        : String((row as Record<string, unknown>)[col.accessor] ?? '—')}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default DataTable;
