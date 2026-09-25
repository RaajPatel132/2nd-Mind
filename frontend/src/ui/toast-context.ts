import { createContext, useContext } from 'react'

export type ToastAction = { label: string; run: () => void }
export type Show = (message: string, action?: ToastAction) => void

export const ToastContext = createContext<Show>(() => undefined)

export function useToast(): Show {
  return useContext(ToastContext)
}
