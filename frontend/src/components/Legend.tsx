export interface LegendItem {
  label: string
  color: string
  kind: 'solid' | 'dashed' | 'area' | 'bar'
}

/** Visible legend that shows line style as well as colour. */
export function Legend({ items }: { items: LegendItem[] }) {
  return (
    <ul className="legend">
      {items.map((item) => (
        <li key={item.label}>
          <svg width="22" height="12" aria-hidden="true">
            {item.kind === 'area' || item.kind === 'bar' ? (
              <rect x="1" y="1" width="20" height="10" rx="2" fill={item.color} fillOpacity={item.kind === 'area' ? 0.3 : 1} />
            ) : (
              <line
                x1="1"
                y1="6"
                x2="21"
                y2="6"
                stroke={item.color}
                strokeWidth="2.5"
                strokeDasharray={item.kind === 'dashed' ? '5 3' : undefined}
              />
            )}
          </svg>
          {item.label}
        </li>
      ))}
    </ul>
  )
}
