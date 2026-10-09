import type { ClipStyleOptions, ClipVersion, ClipVersionList, TitleList, UploadResponse } from '../types'

const API_BASE_URL = (() => {
  const configured = import.meta.env.VITE_API_BASE_URL
  if (configured) return configured.replace(/\/+$/, '')
  if (typeof window !== 'undefined' && window.location.hostname !== 'localhost' && window.location.hostname !== '127.0.0.1') {
    return '/api/v1'
  }
  return 'http://localhost:8000/api/v1'
})()
const REQUEST_TIMEOUT_MS = 20_000

export class ApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  const accessToken = sessionStorage.getItem('cutline.accessToken')
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`)

  let response: Response
  let raw: string
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  try {
    response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers, signal: controller.signal })
    raw = await response.text()
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError('The API did not respond within 20 seconds. Check the API URL and try again.', 408)
    }
    throw new ApiError('Could not reach the API. Check its URL and availability.', 0)
  } finally {
    window.clearTimeout(timeout)
  }

  let body: unknown
  try {
    body = raw ? JSON.parse(raw) : undefined
  } catch {
    body = undefined
  }

  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new Event('cutline:auth-expired'))
    throw new ApiError(readErrorMessage(body) || `Request failed (${response.status}).`, response.status)
  }
  return body as T
}

function readErrorMessage(body: unknown): string | undefined {
  if (!body || typeof body !== 'object') return undefined
  const record = body as Record<string, unknown>
  if (typeof record.detail === 'string') return record.detail
  if (typeof record.message === 'string') return record.message
  if (record.error && typeof record.error === 'object') {
    const error = record.error as Record<string, unknown>
    if (typeof error.message === 'string') return error.message
  }
  return undefined
}

export async function login(email: string, password: string): Promise<string> {
  const form = new URLSearchParams({ username: email, password })
  const result = await request<{ access_token: string; refresh_token: string; token_type: string }>(
    '/auth/login',
    { method: 'POST', body: form },
  )
  return result.access_token
}

export async function registerUser(name: string, email: string, password: string): Promise<void> {
  await request('/auth/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, email, password }),
  })
}

export function listAllTitles(): Promise<TitleList> {
  return request<TitleList>('/titles')
}

export function listJobTitles(jobId: string): Promise<TitleList> {
  return request<TitleList>(`/titles/${encodeURIComponent(jobId)}`)
}

export async function queueClips(jobId: string, titleIds: string[]): Promise<void> {
  await request(`/titles/${encodeURIComponent(jobId)}/clips`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title_ids: titleIds }),
  })
}

export async function listClipVersions(jobId: string, titleId: string): Promise<ClipVersionList> {
  return request<ClipVersionList>(`/titles/${encodeURIComponent(jobId)}/clips/${encodeURIComponent(titleId)}/versions`)
}

export async function createClipVersion(
  jobId: string,
  titleId: string,
  config: Record<string, unknown>,
): Promise<ClipVersion> {
  return request<ClipVersion>(`/titles/${encodeURIComponent(jobId)}/clips/${encodeURIComponent(titleId)}/versions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  })
}

export function getStyleOptions(): Promise<ClipStyleOptions> {
  return request<ClipStyleOptions>('/styles/options')
}

const PREVIEW_TIMEOUT_MS = 40_000

async function requestBlob(path: string, init: RequestInit = {}): Promise<Blob> {
  const headers = new Headers(init.headers)
  const accessToken = sessionStorage.getItem('cutline.accessToken')
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`)

  let response: Response
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), PREVIEW_TIMEOUT_MS)
  try {
    response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers, signal: controller.signal })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError('The style preview took too long to render. Try again.', 408)
    }
    throw new ApiError('Could not reach the API for the style preview.', 0)
  } finally {
    window.clearTimeout(timeout)
  }

  if (!response.ok) {
    const raw = await response.text()
    let body: unknown
    try {
      body = raw ? JSON.parse(raw) : undefined
    } catch {
      body = undefined
    }
    if (response.status === 401) window.dispatchEvent(new Event('cutline:auth-expired'))
    throw new ApiError(readErrorMessage(body) || `Preview failed (${response.status}).`, response.status)
  }
  return response.blob()
}

export function previewVideo(config: Record<string, unknown>): Promise<Blob> {
  return requestBlob('/styles/preview', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  })
}

export async function deleteClip(jobId: string, clipId: string): Promise<void> {
  await request(`/titles/${encodeURIComponent(jobId)}/clips/${encodeURIComponent(clipId)}`, {
    method: 'DELETE',
  })
}

export async function deleteClipVersion(jobId: string, titleId: string, versionId: string): Promise<void> {
  await request(`/titles/${encodeURIComponent(jobId)}/clips/${encodeURIComponent(titleId)}/versions/${encodeURIComponent(versionId)}`, {
    method: 'DELETE',
  })
}

export async function createUpload(file: File): Promise<UploadResponse> {
  return request<UploadResponse>('/upload', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename: file.name, content_type: 'video/mp4', size_bytes: file.size }),
  })
}

export function putVideo(
  file: File,
  upload: UploadResponse['upload'],
  onProgress: (percent: number) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open(upload.method, upload.url)
    for (const [name, value] of Object.entries(upload.headers)) xhr.setRequestHeader(name, value)
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100))
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve()
      else reject(new ApiError(`Video storage rejected the upload (${xhr.status}).`, xhr.status))
    }
    xhr.onerror = () => reject(new ApiError('The video could not reach storage. Check the S3 CORS configuration.', 0))
    xhr.onabort = () => reject(new ApiError('Upload cancelled.', 0))
    xhr.send(file)
  })
}