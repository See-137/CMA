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
}

function DataTable<T>({
  columns,
  data,
  onRowClick,
  emptyTitle = 'No data found',
  emptyDescription = 'There are no records to display.',
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
            <tr className="bg-slate-50 border-b border-slate-200">
              {columns.map((col) => (
                <th
                  key={col.accessor}
                  className="px-4 py-3 text-left text-xs font-semibold text-slate-600 uppercase tracking-wider"
                >
                  {col.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {data.map((row, i) => (
              <tr
                key={i}
                onClick={() => onRowClick?.(row)}
                className={`${
                  onRowClick ? 'cursor-pointer' : ''
                } hover:bg-slate-50 transition-colors`}
              >
                {columns.map((col) => (
                  <td key={col.accessor} className="px-4 py-3 text-slate-700 whitespace-nowrap">
                    {col.render
                      ? col.render(row)
                      : String((row as Record<string, unknown>)[col.accessor] ?? '-')}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default DataTable;
