import type { ReactNode } from "react";

/**
 * Build-time UI slot registry.
 *
 * Plugins may contribute components to a named slot, but the shipped bundle
 * only ever renders components that were compiled into it: there is no remote
 * script loading. The AI视图 shell asks the slot for its tabs instead of
 * hard-coding them, so a new tab is a contribution, not an edit to the shell.
 */
export interface UISlotItem<T> {
  id: string;
  slot: string;
  order: number;
  builtin: boolean;
  data: T;
}

const REGISTRY = new Map<string, UISlotItem<unknown>[]>();

export function registerUISlot<T>(item: {
  id: string;
  slot: string;
  order?: number;
  builtin?: boolean;
  data: T;
}): void {
  const entries = REGISTRY.get(item.slot) ?? [];
  if (entries.some((entry) => entry.id === item.id)) {
    throw new Error(`duplicate UI slot contribution: ${item.slot}/${item.id}`);
  }
  entries.push({
    id: item.id,
    slot: item.slot,
    order: item.order ?? 100,
    builtin: item.builtin ?? false,
    data: item.data,
  });
  entries.sort((left, right) =>
    left.order - right.order || left.id.localeCompare(right.id));
  REGISTRY.set(item.slot, entries);
}

export function uiSlotItems<T>(slot: string): UISlotItem<T>[] {
  return (REGISTRY.get(slot) ?? []) as UISlotItem<T>[];
}

export type SlotRender = () => ReactNode;
