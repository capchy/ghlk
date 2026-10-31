import { useEffect } from 'react'
import { CheckCircle, WarningCircle, X } from '@phosphor-icons/react'

export interface ToastMessage {
  id: number
  tone: 'success' | 'error'
  text: string
}

/** Polite live region: announced without stealing focus; auto-dismisses after 6s. */
export function Toast({ toast, onDismiss }: { toast: ToastMessage | null; onDismiss: () => void }) {
  useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(onDismiss, 6000)
    return () => window.clearTimeout(timer)
  }, [toast, onDismiss])

  return (
    <div className="toast-region" aria-live="polite" role="status">
      {toast && (
        <div className={`toast toast--${toast.tone}`}>
          {toast.tone === 'success' ? <CheckCircle size={20} aria-hidden="true" /> : <WarningCircle size={20} aria-hidden="true" />}
          <span>{toast.text}</span>
          <button type="button" className="btn btn--icon btn--small" onClick={onDismiss} aria-label="Dismiss notification">
            <X size={16} aria-hidden="true" />
          </button>
        </div>
      )}
    </div>
  )
}
