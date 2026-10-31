import { CaretDown, Info } from '@phosphor-icons/react'

/** Data-quality warnings from the API, de-duplicated and collapsed by default. */
export function Warnings({ warnings }: { warnings: string[] }) {
  const unique = [...new Set(warnings)]
  if (unique.length === 0) return null
  return (
    <details className="warnings">
      <summary>
        <Info size={18} aria-hidden="true" />
        <span>
          {unique.length} data-quality note{unique.length === 1 ? '' : 's'} from the API
        </span>
        <CaretDown size={16} aria-hidden="true" className="warnings__caret" />
      </summary>
      <ul>
        {unique.map((w) => (
          <li key={w}>{w}</li>
        ))}
      </ul>
    </details>
  )
}
