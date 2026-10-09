import { ArrowLeft, Check, Palette, RotateCcw, SlidersHorizontal, Sparkles, Wand2 } from 'lucide-react'
import { useEffect, useEffectEvent, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { ApiError, createClipVersion, getStyleOptions, listJobTitles, previewVideo } from '../lib/api'
import type { ClipStyleOptions, Title } from '../types'

type StyleConfigForm = {
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

// Same shape the API returns, used only until the options request lands (or if an
// older backend has no /styles endpoint yet): the page never renders an empty form.
const FALLBACK_OPTIONS: ClipStyleOptions = {
  defaults: {
    display_mode: 'single_word',
    animation: 'pop',
    font_name: 'DejaVu Sans',
    font_size: 56,
    position: 'below center',
    position_x: null,
    position_y: null,
    rotation: 0,
    area_scale: 1,
    bold: true,
    italic: false,
    uppercase: false,
    border_style: 1,
    outline: 3,
    shadow: 2,
    box_opacity: 1,
    font_color: '#FFFFFF',
    outline_color: '#000000',
    shadow_color: '#000000',
    highlight_color: '#00FF00',
    rainbow: false,
    remove_filler_words: true,
    filler_words_list: ['uh', 'um', 'ah', 'hmm', 'oh', 'umm'],
    remove_silence_gaps: true,
    silence_gap_threshold: 0.4,
  },
  display_modes: [
    { value: 'single_word', label: 'Single Word' },
    { value: 'accumulate', label: 'Accumulate' },
    { value: 'karaoke', label: 'Karaoke' },
    { value: 'full', label: 'Full Sentence' },
  ],
  animations: [
    { value: 'none', label: 'None' },
    { value: 'pop', label: 'Pop' },
    { value: 'zoom_in', label: 'Zoom In' },
    { value: 'zoom_out', label: 'Zoom Out' },
    { value: 'fade', label: 'Fade' },
    { value: 'slide_left', label: 'Slide Left' },
    { value: 'slide_right', label: 'Slide Right' },
    { value: 'typewriter', label: 'Typewriter' },
    { value: 'bounce', label: 'Bounce' },
    { value: 'blur_in', label: 'Blur In' },
  ],
  positions: [
    { value: 'top left', label: 'Top Left' },
    { value: 'top center', label: 'Top Center' },
    { value: 'top right', label: 'Top Right' },
    { value: 'middle left', label: 'Middle Left' },
    { value: 'center', label: 'Center' },
    { value: 'middle right', label: 'Middle Right' },
    { value: 'bottom left', label: 'Bottom Left' },
    { value: 'below center', label: 'Below Center' },
    { value: 'bottom right', label: 'Bottom Right' },
  ],
  border_styles: [
    { value: 1, label: 'Outline & shadow' },
    { value: 3, label: 'Background box' },
  ],
  fonts: [
    { value: 'DejaVu Sans', label: 'DejaVu Sans', renders_as: 'DejaVu Sans' },
    { value: 'Arial', label: 'Arial', renders_as: 'Arimo' },
    { value: 'Inter', label: 'Inter', renders_as: 'Inter' },
    { value: 'Montserrat', label: 'Montserrat', renders_as: 'Montserrat' },
    { value: 'Poppins', label: 'Poppins', renders_as: 'Poppins' },
    { value: 'Roboto', label: 'Roboto', renders_as: 'Roboto' },
    { value: 'Oswald', label: 'Oswald', renders_as: 'Oswald' },
    { value: 'Georgia', label: 'Georgia', renders_as: 'Lora' },
    { value: 'Trebuchet MS', label: 'Trebuchet MS', renders_as: 'Lato' },
    { value: 'Anton', label: 'Anton', renders_as: 'Anton' },
    { value: 'Fredoka', label: 'Fredoka', renders_as: 'Fredoka' },
    { value: 'Space Mono', label: 'Space Mono', renders_as: 'Space Mono' },
    { value: 'Courier Prime', label: 'Courier Prime', renders_as: 'Courier Prime' },
    { value: 'Playfair Display', label: 'Playfair Display', renders_as: 'Playfair Display' },
    { value: 'Cinzel', label: 'Cinzel', renders_as: 'Cinzel' },
    { value: 'Cormorant Garamond', label: 'Cormorant Garamond', renders_as: 'Cormorant Garamond' },
  ],
  font_size: { minimum: 8, maximum: 300, step: 1 },
  outline: { minimum: 0, maximum: 20, step: 0.5 },
  shadow: { minimum: 0, maximum: 20, step: 0.5 },
  box_opacity: { minimum: 0, maximum: 1, step: 0.05 },
  silence_gap_threshold: { minimum: 0, maximum: 10, step: 0.1 },
  rotation: { minimum: -45, maximum: 45, step: 1 },
  area_scale: { minimum: 0.5, maximum: 2, step: 0.05 },
  word_rule_font_size: { minimum: 8, maximum: 300, step: 1 },
  word_rule_animations: [],
  max_word_rules: 100,
  max_char_rules: 100,
  max_rule_positions: 200,
}

type StylePreset = {
  id: string
  name: string
  spec: string
  chip: React.ReactNode
  config: Partial<StyleConfigForm>
}

const CHIP_BG = '#242a31'

const STYLE_PRESETS: StylePreset[] = [
  {
    id: 'word-pop',
    name: 'Word pop',
    spec: 'Anton, uppercase, 80-100px. White with a thick black stroke, keyword in yellow. Words snap in as they are spoken.',
    chip: <span style={{ fontFamily: 'Anton, Impact, sans-serif', fontSize: 16, color: '#FFD23F', textTransform: 'uppercase' }}>Habit</span>,
    config: {
      display_mode: 'karaoke',
      animation: 'pop',
      font_name: 'Anton',
      font_size: 90,
      position: 'center',
      position_x: 0.5,
      position_y: 0.44,
      rotation: 0,
      area_scale: 1,
      bold: false,
      italic: false,
      uppercase: true,
      border_style: 1,
      outline: 5,
      shadow: 3,
      box_opacity: 1,
      font_color: '#FFFFFF',
      outline_color: '#000000',
      shadow_color: '#000000',
      highlight_color: '#FFD23F',
      rainbow: false,
    },
  },
  {
    id: 'karaoke',
    name: 'Karaoke',
    spec: 'Montserrat ExtraBold, 60-75px. White text, the active word turns yellow as it is spoken.',
    chip: <span style={{ fontFamily: 'Montserrat, sans-serif', fontWeight: 800, fontSize: 13, color: '#fff', textTransform: 'uppercase' }}>Say <span style={{ color: '#FFD23F' }}>this</span></span>,
    config: {
      display_mode: 'karaoke',
      animation: 'none',
      font_name: 'Montserrat',
      font_size: 68,
      position: 'center',
      position_x: 0.5,
      position_y: 0.46,
      rotation: 0,
      area_scale: 1,
      bold: true,
      italic: false,
      uppercase: true,
      border_style: 1,
      outline: 4,
      shadow: 2,
      box_opacity: 1,
      font_color: '#FFFFFF',
      outline_color: '#000000',
      shadow_color: '#000000',
      highlight_color: '#FFD23F',
      rainbow: false,
    },
  },
  {
    id: 'clean-minimal',
    name: 'Clean minimal',
    spec: 'Poppins Medium, sentence case, 40-55px. White text on a half-transparent black box, simple fade, lower third.',
    chip: <span style={{ fontFamily: 'Poppins, sans-serif', fontSize: 13, color: '#fff', background: 'rgba(0,0,0,.55)', padding: '3px 9px', borderRadius: 4 }}>a simple sentence</span>,
    config: {
      display_mode: 'full',
      animation: 'fade',
      font_name: 'Poppins',
      font_size: 48,
      position: 'below center',
      position_x: 0.5,
      position_y: 0.68,
      rotation: 0,
      area_scale: 1,
      bold: false,
      italic: false,
      uppercase: false,
      border_style: 3,
      outline: 12,
      shadow: 0,
      box_opacity: 0.55,
      font_color: '#FFFFFF',
      outline_color: '#000000',
      shadow_color: '#000000',
      highlight_color: '#00FF00',
      rainbow: false,
    },
  },
  {
    id: 'boxed',
    name: 'Boxed',
    spec: 'Poppins Bold, 55-70px. Black text on a yellow box, the box pops in and grows word by word.',
    chip: <span style={{ fontFamily: 'Poppins, sans-serif', fontWeight: 700, fontSize: 13, color: '#111', background: '#FFD23F', padding: '3px 9px', borderRadius: 7 }}>boxed</span>,
    config: {
      display_mode: 'accumulate',
      animation: 'pop',
      font_name: 'Poppins',
      font_size: 62,
      position: 'center',
      position_x: 0.5,
      position_y: 0.46,
      rotation: 0,
      area_scale: 1,
      bold: true,
      italic: false,
      uppercase: false,
      border_style: 3,
      outline: 16,
      shadow: 0,
      box_opacity: 1,
      font_color: '#111111',
      outline_color: '#FFD23F',
      shadow_color: '#000000',
      highlight_color: '#00FF00',
      rainbow: false,
    },
  },
  {
    id: 'typewriter',
    name: 'Typewriter',
    spec: 'Space Mono Bold, 45-60px. Letters appear one by one behind a cursor. Good for story and tech.',
    chip: <span style={{ fontFamily: "'Space Mono', monospace", fontWeight: 700, fontSize: 13, color: '#7CFFB2' }}>type|</span>,
    config: {
      display_mode: 'accumulate',
      animation: 'typewriter',
      font_name: 'Space Mono',
      font_size: 52,
      position: 'center',
      position_x: 0.5,
      position_y: 0.5,
      rotation: 0,
      area_scale: 1,
      bold: true,
      italic: false,
      uppercase: false,
      border_style: 1,
      outline: 0,
      shadow: 0,
      box_opacity: 1,
      font_color: '#7CFFB2',
      outline_color: '#000000',
      shadow_color: '#000000',
      highlight_color: '#00FF00',
      rainbow: false,
    },
  },
  {
    id: 'typewriter-classic',
    name: 'Typewriter classic',
    spec: 'Courier Prime Bold, 45-60px. The same one-letter-at-a-time reveal on a classic typewriter face.',
    chip: <span style={{ fontFamily: "'Courier Prime', 'Courier New', monospace", fontWeight: 700, fontSize: 13, color: '#7CFFB2' }}>type|</span>,
    config: {
      display_mode: 'accumulate',
      animation: 'typewriter',
      font_name: 'Courier Prime',
      font_size: 50,
      position: 'center',
      position_x: 0.5,
      position_y: 0.5,
      rotation: 0,
      area_scale: 1,
      bold: true,
      italic: false,
      uppercase: false,
      border_style: 1,
      outline: 0,
      shadow: 0,
      box_opacity: 1,
      font_color: '#7CFFB2',
      outline_color: '#000000',
      shadow_color: '#000000',
      highlight_color: '#00FF00',
      rainbow: false,
    },
  },
  {
    id: 'bounce',
    name: 'Bounce',
    spec: 'Fredoka SemiBold, 60-80px. Every word a different bright colour, bouncing in with a slight tilt.',
    chip: (
      <span style={{ fontFamily: 'Fredoka, sans-serif', fontWeight: 600, fontSize: 15 }}>
        <span style={{ color: '#FF5A5F' }}>b</span>
        <span style={{ color: '#FFC53D' }}>o</span>
        <span style={{ color: '#3DDC97' }}>u</span>
        <span style={{ color: '#4D9DE0' }}>n</span>
        <span style={{ color: '#B15EFF' }}>c</span>
        <span style={{ color: '#FF8A3D' }}>e</span>
      </span>
    ),
    config: {
      display_mode: 'accumulate',
      animation: 'bounce',
      font_name: 'Fredoka',
      font_size: 70,
      position: 'center',
      position_x: 0.5,
      position_y: 0.46,
      rotation: 0,
      area_scale: 1,
      bold: true,
      italic: false,
      uppercase: false,
      border_style: 1,
      outline: 4,
      shadow: 2,
      box_opacity: 1,
      font_color: '#FFFFFF',
      outline_color: '#000000',
      shadow_color: '#000000',
      highlight_color: '#00FF00',
      rainbow: true,
    },
  },
  {
    id: 'cine-playfair',
    name: 'Cinematic',
    spec: 'Playfair Display italic, 45-60px. Cream text, no outline, a slow blur-in. Travel and quote edits.',
    chip: <span style={{ fontFamily: "'Playfair Display', Georgia, serif", fontStyle: 'italic', fontSize: 15, color: '#F6EEDC' }}>Cinematic</span>,
    config: {
      display_mode: 'full',
      animation: 'blur_in',
      font_name: 'Playfair Display',
      font_size: 52,
      position: 'below center',
      position_x: 0.5,
      position_y: 0.64,
      rotation: 0,
      area_scale: 1,
      bold: false,
      italic: true,
      uppercase: false,
      border_style: 1,
      outline: 0,
      shadow: 0,
      box_opacity: 1,
      font_color: '#F6EEDC',
      outline_color: '#000000',
      shadow_color: '#000000',
      highlight_color: '#00FF00',
      rainbow: false,
    },
  },
  {
    id: 'cine-cinzel',
    name: 'Cinematic classic',
    spec: 'Cinzel, 45-60px. Roman capitals in cream, no outline, a slow blur-in over quiet footage.',
    chip: <span style={{ fontFamily: 'Cinzel, Georgia, serif', fontSize: 12, color: '#F6EEDC', letterSpacing: 1.5 }}>CINZEL</span>,
    config: {
      display_mode: 'full',
      animation: 'blur_in',
      font_name: 'Cinzel',
      font_size: 50,
      position: 'below center',
      position_x: 0.5,
      position_y: 0.64,
      rotation: 0,
      area_scale: 1,
      bold: false,
      italic: false,
      uppercase: true,
      border_style: 1,
      outline: 0,
      shadow: 0,
      box_opacity: 1,
      font_color: '#F6EEDC',
      outline_color: '#000000',
      shadow_color: '#000000',
      highlight_color: '#00FF00',
      rainbow: false,
    },
  },
  {
    id: 'cine-cormorant',
    name: 'Cinematic elegant',
    spec: 'Cormorant Garamond italic, 45-65px. Airy cream serif, no outline, slow blur-in for poetic edits.',
    chip: <span style={{ fontFamily: "'Cormorant Garamond', Georgia, serif", fontStyle: 'italic', fontSize: 17, color: '#F6EEDC' }}>Elegant</span>,
    config: {
      display_mode: 'full',
      animation: 'blur_in',
      font_name: 'Cormorant Garamond',
      font_size: 56,
      position: 'below center',
      position_x: 0.5,
      position_y: 0.64,
      rotation: 0,
      area_scale: 1,
      bold: false,
      italic: true,
      uppercase: false,
      border_style: 1,
      outline: 0,
      shadow: 0,
      box_opacity: 1,
      font_color: '#F6EEDC',
      outline_color: '#000000',
      shadow_color: '#000000',
      highlight_color: '#00FF00',
      rainbow: false,
    },
  },
]

type MotionGroup = { label: string; options: { value: string; label: string }[] }

const MOTION_GROUPS: MotionGroup[] = [
  { label: 'None', options: [{ value: 'none', label: 'No motion' }] },
  { label: 'Pop', options: [{ value: 'pop', label: 'Pop' }] },
  { label: 'Zoom', options: [{ value: 'zoom_in', label: 'Zoom in' }, { value: 'zoom_out', label: 'Zoom out' }] },
  { label: 'Fade', options: [{ value: 'fade', label: 'Fade' }] },
  { label: 'Slide', options: [{ value: 'slide_right', label: 'From left' }, { value: 'slide_left', label: 'From right' }] },
  { label: 'Typewriter', options: [{ value: 'typewriter', label: 'Typewriter' }] },
  { label: 'Bounce', options: [{ value: 'bounce', label: 'Bounce' }] },
  { label: 'Blur in', options: [{ value: 'blur_in', label: 'Blur in' }] },
]

// Rough centre of the text a registered anchor lands on, as a fraction of the frame.
// Only used to place the drag box until the user moves it; after that the exact
// position_x/position_y the widget sends is what the box shows.
const ANCHOR_POINTS: Record<string, { x: number; y: number }> = {
  'top left': { x: 0.26, y: 0.1 },
  'top center': { x: 0.5, y: 0.1 },
  'top right': { x: 0.74, y: 0.1 },
  'middle left': { x: 0.26, y: 0.5 },
  center: { x: 0.5, y: 0.5 },
  'middle right': { x: 0.74, y: 0.5 },
  'bottom left': { x: 0.26, y: 0.9 },
  'below center': { x: 0.5, y: 0.9 },
  'bottom right': { x: 0.74, y: 0.9 },
}

type DragState =
  | { kind: 'move'; px: number; py: number; ox: number; oy: number }
  | { kind: 'resize'; dist: number; scale: number; cx: number; cy: number }
  | { kind: 'rotate'; angle: number; rotation: number; cx: number; cy: number }

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value))
}

function centerOf(config: StyleConfigForm): { x: number; y: number } {
  if (config.position_x !== null && config.position_y !== null) {
    return { x: config.position_x, y: config.position_y }
  }
  return ANCHOR_POINTS[config.position] ?? { x: 0.5, y: 0.9 }
}

function fromDefaults(defaults: ClipStyleOptions['defaults']): StyleConfigForm {
  return { ...defaults, filler_words_list: [...defaults.filler_words_list] }
}

export function ClipStylePage() {
  const { jobId = '', titleId = '' } = useParams()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const titlesParam = searchParams.get('titles') ?? ''
  const batchIds = titlesParam.split(',').map((id) => id.trim()).filter(Boolean)
  const batchMode = batchIds.length > 0
  const [title, setTitle] = useState<Title | null>(null)
  const [titles, setTitles] = useState<Title[]>([])
  const [options, setOptions] = useState<ClipStyleOptions>(FALLBACK_OPTIONS)
  const [usingFallback, setUsingFallback] = useState(false)
  const [config, setConfig] = useState<StyleConfigForm>(() => fromDefaults(FALLBACK_OPTIONS.defaults))
  const [activePreset, setActivePreset] = useState<string | null>(null)
  const [subChoice, setSubChoice] = useState<Record<string, string>>({})
  const [safeZones, setSafeZones] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  // The options request races the user: whoever lands first sets the starting values,
  // so a slow response must not overwrite a style already being built.
  const edited = useRef(false)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [previewBusy, setPreviewBusy] = useState(false)
  const [previewFailed, setPreviewFailed] = useState(false)
  const previewObjectUrl = useRef<string | null>(null)
  const frameRef = useRef<HTMLDivElement | null>(null)
  const dragRef = useRef<DragState | null>(null)

  useEffect(() => {
    let cancelled = false
    void getStyleOptions()
      .then((result) => {
        if (cancelled) return
        setOptions(result)
        if (!edited.current) setConfig(fromDefaults(result.defaults))
      })
      .catch(() => {
        if (!cancelled) setUsingFallback(true)
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    async function loadTitle() {
      if (!jobId) return
      setLoading(true)
      setError('')
      try {
        const result = await listJobTitles(jobId)
        if (titlesParam) {
          const ids = titlesParam.split(',').map((id) => id.trim()).filter(Boolean)
          setTitles(result.items.filter((item) => ids.includes(item.id)))
        } else if (titleId) {
          const match = result.items.find((item) => item.id === titleId) ?? null
          setTitle(match)
        }
      } catch (reason) {
        setError(reason instanceof ApiError && reason.status === 404 ? 'This title is not available for your project.' : reason instanceof Error ? reason.message : 'Could not load this title.')
      } finally {
        setLoading(false)
      }
    }
    void loadTitle()
  }, [jobId, titleId, titlesParam])

  // The preview is a short clip rendered by the real subtitle pipeline, so it is a
  // pixel-true look at what the generated clip will burn; rebuild it on every change.
  const refreshPreview = useEffectEvent(async () => {
    setPreviewBusy(true)
    try {
      const data = await previewVideo(config)
      if (previewObjectUrl.current) URL.revokeObjectURL(previewObjectUrl.current)
      const url = URL.createObjectURL(new Blob([data], { type: 'video/mp4' }))
      previewObjectUrl.current = url
      setPreviewUrl(url)
      setPreviewFailed(false)
    } catch {
      setPreviewFailed(true)
    } finally {
      setPreviewBusy(false)
    }
  })

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void refreshPreview()
    }, 450)
    return () => window.clearTimeout(timer)
  }, [config])

  useEffect(
    () => () => {
      if (previewObjectUrl.current) URL.revokeObjectURL(previewObjectUrl.current)
    },
    [],
  )

  function patchConfig(patch: Partial<StyleConfigForm>) {
    edited.current = true
    setActivePreset(null)
    setConfig((previous) => ({ ...previous, ...patch }))
  }

  function updateField<K extends keyof StyleConfigForm>(key: K, value: StyleConfigForm[K]) {
    patchConfig({ [key]: value } as Partial<StyleConfigForm>)
  }

  function applyPreset(preset: StylePreset) {
    edited.current = true
    setActivePreset(preset.id)
    setConfig((previous) => ({ ...previous, ...preset.config }))
  }

  function pickMotionGroup(group: MotionGroup) {
    if (group.options.some((option) => option.value === config.animation)) return
    patchConfig({ animation: subChoice[group.label] ?? group.options[0].value })
  }

  function pickMotionOption(group: MotionGroup, value: string) {
    setSubChoice((previous) => ({ ...previous, [group.label]: value }))
    patchConfig({ animation: value })
  }

  function pointOf(event: React.PointerEvent): { x: number; y: number } {
    const rect = frameRef.current?.getBoundingClientRect()
    if (!rect || rect.width === 0 || rect.height === 0) return { x: 0.5, y: 0.5 }
    return {
      x: clamp((event.clientX - rect.left) / rect.width, 0, 1),
      y: clamp((event.clientY - rect.top) / rect.height, 0, 1),
    }
  }

  function beginMove(event: React.PointerEvent<HTMLDivElement>) {
    event.preventDefault()
    const point = pointOf(event)
    const center = centerOf(config)
    if (config.position_x === null || config.position_y === null) {
      patchConfig({ position_x: Number(center.x.toFixed(3)), position_y: Number(center.y.toFixed(3)) })
    }
    dragRef.current = { kind: 'move', px: point.x, py: point.y, ox: center.x, oy: center.y }
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  function beginResize(event: React.PointerEvent<HTMLButtonElement>) {
    event.stopPropagation()
    event.preventDefault()
    const point = pointOf(event)
    const center = centerOf(config)
    dragRef.current = {
      kind: 'resize',
      dist: Math.max(0.02, Math.hypot(point.x - center.x, point.y - center.y)),
      scale: config.area_scale,
      cx: center.x,
      cy: center.y,
    }
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  function beginRotate(event: React.PointerEvent<HTMLButtonElement>) {
    event.stopPropagation()
    event.preventDefault()
    const point = pointOf(event)
    const center = centerOf(config)
    dragRef.current = {
      kind: 'rotate',
      angle: (Math.atan2(point.y - center.y, point.x - center.x) * 180) / Math.PI,
      rotation: config.rotation,
      cx: center.x,
      cy: center.y,
    }
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  function moveDrag(event: React.PointerEvent) {
    const drag = dragRef.current
    if (!drag) return
    const point = pointOf(event)
    if (drag.kind === 'move') {
      const x = clamp(drag.ox + (point.x - drag.px), 0.03, 0.97)
      const y = clamp(drag.oy + (point.y - drag.py), 0.05, 0.95)
      patchConfig({ position_x: Number(x.toFixed(3)), position_y: Number(y.toFixed(3)) })
    } else if (drag.kind === 'resize') {
      const distance = Math.hypot(point.x - drag.cx, point.y - drag.cy)
      const raw = (drag.scale * distance) / drag.dist
      const scale = clamp(Math.round(raw / 0.05) * 0.05, 0.5, 2)
      patchConfig({ area_scale: Number(scale.toFixed(2)) })
    } else if (drag.kind === 'rotate') {
      const angle = (Math.atan2(point.y - drag.cy, point.x - drag.cx) * 180) / Math.PI
      const rotation = clamp(Math.round(drag.rotation + (angle - drag.angle)), -45, 45)
      patchConfig({ rotation })
    }
  }

  function endDrag() {
    dragRef.current = null
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!jobId || (!titleId && !batchMode)) return
    setSaving(true)
    setError('')
    try {
      const payload = {
        ...config,
        filler_words_list: config.filler_words_list.filter((word) => word.trim().length > 0),
      }
      const ids = batchMode ? batchIds : [titleId]
      await Promise.all(ids.map((id) => createClipVersion(jobId, id, payload)))
      navigate(`/projects/${encodeURIComponent(jobId)}/clips`, { replace: true })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to generate the styled clip.')
    } finally {
      setSaving(false)
    }
  }

  const choiceLabel = (choices: ClipStyleOptions['display_modes'], value: string | number) =>
    choices.find((choice) => choice.value === value)?.label ?? String(value)

  const activeGroup = MOTION_GROUPS.find((group) => group.options.some((option) => option.value === config.animation)) ?? null
  const groupedValues = new Set(MOTION_GROUPS.flatMap((group) => group.options.map((option) => option.value)))
  const extraAnimations = options.animations.filter((animation) => !groupedValues.has(String(animation.value)))
  const customPlacement = config.position_x !== null && config.position_y !== null
  const center = centerOf(config)

  return (
    <div className="page-content narrow-page">
      <Link className="back-link" to={batchMode ? `/projects/${encodeURIComponent(jobId)}` : `/projects/${encodeURIComponent(jobId)}/clips`}><ArrowLeft size={16} /> {batchMode ? 'Back to project' : 'Back to clips'}</Link>

      <header className="project-heading">
        <div className="project-title-block">
          <span className="project-heading-icon"><Wand2 size={20} /></span>
          <div>
            <p className="eyebrow">STYLE STUDIO</p>
            <h1>{batchMode ? `Style ${batchIds.length} ${batchIds.length === 1 ? 'title' : 'titles'}` : (title?.title ?? 'Customize a clip')}</h1>
            <p className="heading-subtitle">{batchMode ? 'The treatment you build below is applied to every clip you selected.' : 'Start from a preset, then tune motion, typography, and placement.'}</p>
            {batchMode && titles.length > 0 && <div className="batch-titles">{titles.map((item) => <span key={item.id}>{item.title}</span>)}</div>}
          </div>
        </div>
        <Link className="secondary-button" to={`/projects/${encodeURIComponent(jobId)}`}><Palette size={15} /> Open project</Link>
      </header>

      {error && <div className="inline-error" role="alert">{error}</div>}
      {usingFallback && !error && <div className="inline-note" role="status">The style catalog could not be loaded, so the built-in options are shown.</div>}

      {loading ? <div className="loading-state"><span className="loader" />Preparing style options</div> : (
        <div className="style-layout">
          <form className="style-form" onSubmit={handleSubmit}>
            <section className="style-panel">
              <div className="panel-heading-row">
                <h3>Style presets</h3>
                {activePreset && <span className="preset-active-tag">{STYLE_PRESETS.find((preset) => preset.id === activePreset)?.name}</span>}
              </div>
              <p className="panel-hint">Pick a look from the preview gallery — every control below updates to match it, and stays yours to tune.</p>
              <div className="preset-grid">
                {STYLE_PRESETS.map((preset) => (
                  <button
                    key={preset.id}
                    type="button"
                    className={`preset-card${activePreset === preset.id ? ' selected' : ''}`}
                    onClick={() => applyPreset(preset)}
                    aria-pressed={activePreset === preset.id}
                  >
                    <span className="preset-chip" style={{ background: CHIP_BG }}>{preset.chip}</span>
                    <strong>{preset.name}</strong>
                    <small>{preset.spec}</small>
                  </button>
                ))}
              </div>
            </section>

            <section className="style-panel">
              <h3>Subtitle behavior</h3>
              <div className="style-grid">
                {options.display_modes.map((mode) => (
                  <button key={mode.value} type="button" className={`option-card${config.display_mode === mode.value ? ' selected' : ''}`} onClick={() => updateField('display_mode', String(mode.value))}>
                    {mode.label}
                  </button>
                ))}
              </div>
            </section>

            <section className="style-panel">
              <h3>Motion</h3>
              <div className="style-grid">
                {MOTION_GROUPS.map((group) => (
                  <button key={group.label} type="button" className={`option-card${activeGroup?.label === group.label ? ' selected' : ''}`} onClick={() => pickMotionGroup(group)}>
                    {group.label}
                  </button>
                ))}
                {extraAnimations.map((animation) => (
                  <button key={String(animation.value)} type="button" className={`option-card${config.animation === String(animation.value) ? ' selected' : ''}`} onClick={() => patchConfig({ animation: String(animation.value) })}>
                    {animation.label}
                  </button>
                ))}
              </div>
              {activeGroup && activeGroup.options.length > 1 && (
                <div className="motion-subrow">
                  <span className="motion-subrow-label">{activeGroup.label}</span>
                  {activeGroup.options.map((option) => (
                    <button key={option.value} type="button" className={`motion-pill${config.animation === option.value ? ' selected' : ''}`} onClick={() => pickMotionOption(activeGroup, option.value)}>
                      {option.label}
                    </button>
                  ))}
                </div>
              )}
            </section>

            <section className="style-panel">
              <h3>Typography</h3>
              <div className="option-row">
                <div className="field-stack">
                  <label>Font family</label>
                  <select value={config.font_name} onChange={(event) => updateField('font_name', event.target.value)}>
                    {options.fonts.map((font) => <option key={font.value} value={font.value}>{font.label}</option>)}
                  </select>
                </div>
                <div className="slider-wrap">
                  <label><span>Font size</span><strong>{config.font_size}px</strong></label>
                  <input type="range" min={options.font_size.minimum} max={options.font_size.maximum} step={options.font_size.step} value={config.font_size} onChange={(event) => updateField('font_size', Number(event.target.value))} />
                </div>
                <div className="style-grid">
                  <button type="button" className={`option-card${config.bold ? ' selected' : ''}`} onClick={() => updateField('bold', !config.bold)}>
                    Bold
                  </button>
                  <button type="button" className={`option-card${config.italic ? ' selected' : ''}`} onClick={() => updateField('italic', !config.italic)}>
                    Italic
                  </button>
                  <button type="button" className={`option-card${config.uppercase ? ' selected' : ''}`} onClick={() => updateField('uppercase', !config.uppercase)}>
                    Uppercase
                  </button>
                </div>
              </div>
            </section>

            <section className="style-panel">
              <div className="panel-heading-row">
                <h3>Placement</h3>
                <button type="button" className="text-button" onClick={() => patchConfig({ position_x: null, position_y: null, rotation: 0, area_scale: 1 })}><RotateCcw size={13} /> Reset placement</button>
              </div>
              <p className="panel-hint">Drag the subtitle box on the preview to place it, pull its corner to scale the area, or use the top handle to tilt it.</p>
              <div className="option-row">
                <div className="field-stack">
                  <label>Anchor</label>
                  <select value={customPlacement ? '__custom' : config.position} onChange={(event) => { if (event.target.value !== '__custom') patchConfig({ position: event.target.value, position_x: null, position_y: null }) }}>
                    {customPlacement && <option value="__custom">Custom (dragged)</option>}
                    {options.positions.map((position) => <option key={position.value} value={position.value}>{position.label}</option>)}
                  </select>
                </div>
                <div className="slider-wrap">
                  <label><span>Rotation</span><strong>{config.rotation}°</strong></label>
                  <input type="range" min={options.rotation.minimum} max={options.rotation.maximum} step={options.rotation.step} value={config.rotation} onChange={(event) => updateField('rotation', Number(event.target.value))} />
                </div>
                <div className="slider-wrap">
                  <label><span>Area scale</span><strong>{config.area_scale}×</strong></label>
                  <input type="range" min={options.area_scale.minimum} max={options.area_scale.maximum} step={options.area_scale.step} value={config.area_scale} onChange={(event) => updateField('area_scale', Number(event.target.value))} />
                </div>
              </div>
            </section>

            <section className="style-panel">
              <h3>Color & edge</h3>
              <div className="color-row">
                <label className="color-swatch"><input type="color" value={config.font_color} onChange={(event) => updateField('font_color', event.target.value)} /> Text</label>
                <label className="color-swatch"><input type="color" value={config.outline_color} onChange={(event) => updateField('outline_color', event.target.value)} /> Outline</label>
                <label className="color-swatch"><input type="color" value={config.shadow_color} onChange={(event) => updateField('shadow_color', event.target.value)} /> Shadow</label>
                <label className="color-swatch"><input type="color" value={config.highlight_color} onChange={(event) => updateField('highlight_color', event.target.value)} /> Accent</label>
              </div>
              <div className="slider-wrap" style={{ marginTop: 14 }}>
                <label><span>Outline width</span><strong>{config.outline}</strong></label>
                <input type="range" min={options.outline.minimum} max={options.outline.maximum} step={options.outline.step} value={config.outline} onChange={(event) => updateField('outline', Number(event.target.value))} />
              </div>
              <div className="slider-wrap">
                <label><span>Shadow strength</span><strong>{config.shadow}</strong></label>
                <input type="range" min={options.shadow.minimum} max={options.shadow.maximum} step={options.shadow.step} value={config.shadow} onChange={(event) => updateField('shadow', Number(event.target.value))} />
              </div>
              <div className="field-stack" style={{ marginTop: 12 }}>
                <label>Border style</label>
                <select value={config.border_style} onChange={(event) => updateField('border_style', Number(event.target.value))}>
                  {options.border_styles.map((style) => <option key={style.value} value={style.value}>{style.label}</option>)}
                </select>
              </div>
              {config.border_style === 3 && (
                <div className="slider-wrap" style={{ marginTop: 14 }}>
                  <label><span>Box opacity</span><strong>{Math.round(config.box_opacity * 100)}%</strong></label>
                  <input type="range" min={options.box_opacity.minimum} max={options.box_opacity.maximum} step={options.box_opacity.step} value={config.box_opacity} onChange={(event) => updateField('box_opacity', Number(event.target.value))} />
                </div>
              )}
              <div className="toggle-row" style={{ marginTop: 8 }}>
                <label>Rainbow words<small>Each word takes the next colour of a bright palette.</small></label>
                <input className="checkbox-toggle" type="checkbox" checked={config.rainbow} onChange={(event) => updateField('rainbow', event.target.checked)} />
              </div>
            </section>

            <section className="style-panel">
              <h3>Cleanup & audio</h3>
              <div className="toggle-row">
                <label>Remove filler words</label>
                <input className="checkbox-toggle" type="checkbox" checked={config.remove_filler_words} onChange={(event) => updateField('remove_filler_words', event.target.checked)} />
              </div>
              <div className="toggle-row">
                <label>Remove silence gaps</label>
                <input className="checkbox-toggle" type="checkbox" checked={config.remove_silence_gaps} onChange={(event) => updateField('remove_silence_gaps', event.target.checked)} />
              </div>
              <div className="slider-wrap" style={{ marginTop: 10 }}>
                <label><span>Silence gap threshold</span><strong>{config.silence_gap_threshold}s</strong></label>
                <input type="range" min={options.silence_gap_threshold.minimum} max={options.silence_gap_threshold.maximum} step={options.silence_gap_threshold.step} value={config.silence_gap_threshold} onChange={(event) => updateField('silence_gap_threshold', Number(event.target.value))} />
              </div>
              <div className="field-stack" style={{ marginTop: 12 }}>
                <label>Filler words</label>
                <textarea value={config.filler_words_list.join(', ')} onChange={(event) => updateField('filler_words_list', event.target.value.split(',').map((word) => word.trim()).filter(Boolean))} />
              </div>
            </section>

            <div className="style-panel-actions">
              <button className="primary-button" type="submit" disabled={saving}>{saving ? 'Generating…' : (batchMode ? `Generate styled clips (${batchIds.length})` : 'Generate styled clip')} <Sparkles size={15} /></button>
            </div>
          </form>

          <aside className="preview-panel">
            <div className="style-preview">
              <div className="style-panel" style={{ padding: 14 }}>
                <h3><SlidersHorizontal size={14} style={{ verticalAlign: 'middle', marginRight: 6 }} /> Live preview</h3>
              </div>
              <div className="preview-stage" aria-label="Subtitle style preview">
                <div className="preview-frame" ref={frameRef}>
                  {previewUrl ? (
                    <video className="preview-video" src={previewUrl} muted loop autoPlay playsInline />
                  ) : (
                    <span className="preview-placeholder">Rendering preview…</span>
                  )}
                  <div className="overlay-layer">
                    {safeZones && (
                      <>
                        <div className="safe-zone safe-zone-right" />
                        <div className="safe-zone safe-zone-bottom" />
                      </>
                    )}
                    <div
                      className="subtitle-box"
                      style={{
                        left: `${center.x * 100}%`,
                        top: `${center.y * 100}%`,
                        width: `${64 * config.area_scale}%`,
                        height: `${16 * config.area_scale}%`,
                        transform: `translate(-50%, -50%) rotate(${config.rotation}deg)`,
                      }}
                      onPointerDown={beginMove}
                      onPointerMove={moveDrag}
                      onPointerUp={endDrag}
                      onPointerCancel={endDrag}
                    >
                      <span className="subtitle-box-tag">Subtitle area{config.area_scale !== 1 ? ` · ${config.area_scale}×` : ''}</span>
                      <button type="button" className="box-handle box-handle-rotate" aria-label="Rotate subtitle area" onPointerDown={beginRotate} onPointerMove={moveDrag} onPointerUp={endDrag} onPointerCancel={endDrag} />
                      <button type="button" className="box-handle box-handle-resize" aria-label="Resize subtitle area" onPointerDown={beginResize} onPointerMove={moveDrag} onPointerUp={endDrag} onPointerCancel={endDrag} />
                    </div>
                  </div>
                </div>
                {previewBusy && <span className="preview-badge">Rendering preview…</span>}
                {previewFailed && <span className="preview-badge preview-badge-error">Preview unavailable</span>}
              </div>
              <div className="preview-toolbar">
                <button type="button" className={`chip-toggle${safeZones ? ' on' : ''}`} onClick={() => setSafeZones((value) => !value)}>Platform safe zones</button>
                <span className="preview-hint">Drag the box to move</span>
              </div>
              <div className="preview-meta">
                <span><strong>Mode</strong> {choiceLabel(options.display_modes, config.display_mode)}</span>
                <span><strong>Motion</strong> {choiceLabel(options.animations, config.animation)}</span>
              </div>
              <div className="preview-meta">
                <span><strong>Position</strong> {customPlacement ? 'Custom (dragged)' : choiceLabel(options.positions, config.position)}</span>
                <span><strong>Font</strong> {config.font_name}</span>
              </div>
              <div className="style-panel-actions" style={{ marginTop: 0 }}>
                <button type="button" className="secondary-button" onClick={() => { edited.current = false; setActivePreset(null); setConfig(fromDefaults(options.defaults)) }}><Check size={14} /> Reset style</button>
              </div>
            </div>
          </aside>
        </div>
      )}
    </div>
  )
}
