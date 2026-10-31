import { useMemo, useState, type ReactNode } from 'react'

export interface Column<T> {
  key: string
  header: string
  render: (row: T) => ReactNode
  /** Value used for sorting; omit to make the column unsortable. */
  sortValue?: (row: T) => number | string
  numeric?: boolean
}

interface DataTableProps<T> {
  caption: string
  columns: Column<T>[]
  rows: T[]
  rowKey: (row: T) => string
}

/** Accessible, sortable table used as the text alternative to every chart. */
export function DataTable<T>({ caption, columns, rows, rowKey }: DataTableProps<T>) {
  const [sort, setSort] = useState<{ key: string; dir: 'ascending' | 'descending' } | null>(null)

  const sorted = useMemo(() => {
    const col = columns.find((c) => c.key === sort?.key)
    if (!sort || !col?.sortValue) return rows
    const get = col.sortValue
    const factor = sort.dir === 'ascending' ? 1 : -1
    return [...rows].sort((a, b) => (get(a) > get(b) ? factor : get(a) < get(b) ? -factor : 0))
  }, [rows, columns, sort])

  return (
    <table className="data-table">
      <caption className="sr-only">{caption}</caption>
      <thead>
        <tr>
          {columns.map((col) => {
            const active = sort?.key === col.key
            return (
              <th
                key={col.key}
                scope="col"
                className={col.numeric ? 'num' : undefined}
                aria-sort={active ? sort.dir : col.sortValue ? 'none' : undefined}
              >
                {col.sortValue ? (
                  <button
                    type="button"
                    className="th-sort"
                    onClick={() =>
                      setSort({ key: col.key, dir: active && sort.dir === 'ascending' ? 'descending' : 'ascending' })
                    }
                  >
                    {col.header}
                    <span aria-hidden="true">{active ? (sort.dir === 'ascending' ? ' ▲' : ' ▼') : ''}</span>
                  </button>
                ) : (
                  col.header
                )}
              </th>
            )
          })}
        </tr>
      </thead>
      <tbody>
        {sorted.map((row) => (
          <tr key={rowKey(row)}>
            {columns.map((col) => (
              <td key={col.key} className={col.numeric ? 'num' : undefined}>
                {col.render(row)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}
