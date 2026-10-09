export type JobStatus = 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED'
export type ClipStatus = 'PENDING' | 'RENDERING' | 'READY' | 'FAILED'

export interface Job {
  id: string
  status: JobStatus
  video_s3_key: string | null
  created_at: string
  updated_at: string
}

export interface Title {
  id: string
  job_id: string
  title: string
  start_time: string
  end_time: string
  duration: string
  status: ClipStatus
  clip_s3_key: string | null
  download_url: string | null
  error_message: string | null
  created_at: string
}

export interface TitleList {
  items: Title[]
  total: number
}

export interface ClipVersion {
  id: string
  title_id: string
  job_id: string
  title: string
  start_time: string
  end_time: string
  duration: string
  status: ClipStatus
  clip_s3_key: string | null
  download_url: string | null
  error_message: string | null
  created_at: string
}

export interface ClipVersionList {
  items: ClipVersion[]
  total: number
}

export interface UploadResponse {
  job: Job
  upload: {
    url: string
    method: string
    headers: Record<string, string>
    key: string
    expires_in: number
  }
}

export interface StyleChoice {
  value: string | number
  label: string
}

export interface FontChoice {
  value: string
  label: string
  renders_as: string
}

export interface NumericRange {
  minimum: number
  maximum: number
  step: number
}

export interface SubtitleStyleDefaults {
  display_mode: string
  animation: string
  font_name: string
  font_size: number
  position: string
  position_x: number | null
  position_y: number | null
  rotation: number
  area_scale: number
  bold: boolean
  italic: boolean
  uppercase: boolean
  border_style: number
  outline: number
  shadow: number
  box_opacity: number
  font_color: string
  outline_color: string
  shadow_color: string
  highlight_color: string
  rainbow: boolean
  remove_filler_words: boolean
  filler_words_list: string[]
  remove_silence_gaps: boolean
  silence_gap_threshold: number
}

export interface ClipStyleOptions {
  defaults: SubtitleStyleDefaults
  display_modes: StyleChoice[]
  animations: StyleChoice[]
  positions: StyleChoice[]
  border_styles: StyleChoice[]
  fonts: FontChoice[]
  font_size: NumericRange
  outline: NumericRange
  shadow: NumericRange
  box_opacity: NumericRange
  silence_gap_threshold: NumericRange
  rotation: NumericRange
  area_scale: NumericRange
  word_rule_font_size: NumericRange
  word_rule_animations: StyleChoice[]
  max_word_rules: number
  max_char_rules: number
  max_rule_positions: number
}

export interface StoredJob {
  id: string
  filename: string
  status: JobStatus
  created_at: string
}