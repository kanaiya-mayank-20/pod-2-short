/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string
  readonly VITE_MAX_VIDEO_SIZE_MB?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}