/** Opening one memory (its detail and editor) from anywhere: a citation, a diff entry, Upcoming. */
import { createContext, useContext } from 'react'

export type ItemField = 'date' | 'kind' | 'category' | 'layer'

export type ItemActions = {
  open: (itemId: string, focus?: ItemField) => void
}

export const ItemContext = createContext<ItemActions | null>(null)

export function useItemActions(): ItemActions | null {
  return useContext(ItemContext)
}
