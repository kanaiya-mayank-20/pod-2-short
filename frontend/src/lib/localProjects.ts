import type { StoredJob } from '../types'

function currentScope(): string {
  const token = sessionStorage.getItem('cutline.accessToken')
  if (!token) return 'anonymous'
  try {
    const payload = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')
    const claims = JSON.parse(atob(payload)) as { sub?: string }
    return claims.sub || 'unknown'
  } catch {
    return 'unknown'
  }
}

function storageKey(kind: 'projects' | 'queued'): string {
  return `cutline.${kind}.${currentScope()}`
}

export function getStoredProjects(): StoredJob[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(storageKey('projects')) || '[]')
    return Array.isArray(value) ? (value as StoredJob[]) : []
  } catch {
    return []
  }
}

export function saveProject(project: StoredJob): void {
  try {
    const projects = getStoredProjects().filter((item) => item.id !== project.id)
    localStorage.setItem(storageKey('projects'), JSON.stringify([project, ...projects]))
  } catch {
    // The API flow must still succeed when browser storage is unavailable.
  }
}

export function getQueuedTitleIds(jobId: string): string[] {
  try {
    const value = JSON.parse(localStorage.getItem(storageKey('queued')) || '{}') as Record<string, unknown>
    const ids = value[jobId]
    return Array.isArray(ids) ? ids.filter((id): id is string => typeof id === 'string') : []
  } catch {
    return []
  }
}

export function markTitlesQueued(jobId: string, titleIds: string[]): void {
  try {
    const queued = readQueuedMap()
    queued[jobId] = [...new Set([...(queued[jobId] || []), ...titleIds])]
    localStorage.setItem(storageKey('queued'), JSON.stringify(queued))
  } catch {
    // The queue request is authoritative; local metadata is only a UI convenience.
  }
}

export function clearQueuedTitle(jobId: string, titleId: string): void {
  try {
    const queued = readQueuedMap()
    queued[jobId] = (queued[jobId] || []).filter((id) => id !== titleId)
    localStorage.setItem(storageKey('queued'), JSON.stringify(queued))
  } catch {
    // The queue status remains available from the API whenever it changes.
  }
}

function readQueuedMap(): Record<string, string[]> {
  try {
    return JSON.parse(localStorage.getItem(storageKey('queued')) || '{}') as Record<string, string[]>
  } catch {
    return {}
  }
}