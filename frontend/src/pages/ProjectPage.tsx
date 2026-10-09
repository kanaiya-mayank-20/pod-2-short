import { ArrowLeft, ArrowUpRight, Check, CircleAlert, Clock3, Download, Film, RefreshCw, Scissors, Sparkles } from 'lucide-react'
import { useEffect, useEffectEvent, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { SubtitleChoiceModal } from '../components/SubtitleChoiceModal'
import { ApiError, listJobTitles, queueClips } from '../lib/api'
import { clearQueuedTitle, getQueuedTitleIds, getStoredProjects, markTitlesQueued } from '../lib/localProjects'
import type { ClipStatus, Title } from '../types'

function isUnavailable(title: Title, queuedIds: string[]): boolean {
  return title.status === 'READY' || title.status === 'RENDERING' || (title.status === 'PENDING' && queuedIds.includes(title.id))
}

function renderState(status: ClipStatus, queuedLocally: boolean): { label: string; tone: string } {
  if (status === 'READY') return { label: 'Clip ready', tone: 'ready' }
  if (status === 'RENDERING') return { label: 'Rendering', tone: 'working' }
  if (status === 'FAILED') return { label: 'Render failed', tone: 'failed' }
  if (queuedLocally) return { label: 'In queue', tone: 'working' }
  return { label: 'Available', tone: 'available' }
}

function seconds(value: string): string {
  const total = Number(value)
  return `${Math.floor(total / 60)}:${String(Math.floor(total % 60)).padStart(2, '0')}`
}

export function ProjectPage() {
  const { jobId = '' } = useParams()
  const navigate = useNavigate()
  const [titles, setTitles] = useState<Title[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [queuedIds, setQueuedIds] = useState<string[]>(() => getQueuedTitleIds(jobId))
  const [loadedJobId, setLoadedJobId] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const [queueing, setQueueing] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState(new URLSearchParams(window.location.search).get('uploaded') === '1' ? 'Video uploaded successfully. Analysis will add titles here when it is ready.' : '')
  const [choiceOpen, setChoiceOpen] = useState(false)
  const [choiceIds, setChoiceIds] = useState<string[]>([])

  async function loadTitles(isRefresh = false) {
    if (isRefresh) {
      setRefreshing(true)
      setError('')
    }
    try {
      const result = await listJobTitles(jobId)
      setError('')
      setTitles(result.items)
      setLoadedJobId(jobId)
      for (const title of result.items) {
        if (title.status === 'FAILED') clearQueuedTitle(jobId, title.id)
      }
      setQueuedIds(getQueuedTitleIds(jobId))
    } catch (reason) {
      setLoadedJobId(jobId)
      setError(reason instanceof ApiError && reason.status === 404 ? 'This project could not be found for your account.' : reason instanceof Error ? reason.message : 'Could not load project titles.')
    } finally {
      setRefreshing(false)
    }
  }

  const initializeProject = useEffectEvent(() => {
    void loadTitles()
    const next = new URLSearchParams(window.location.search)
    if (next.has('uploaded')) {
      next.delete('uploaded')
      const query = next.toString()
      window.history.replaceState(window.history.state, '', `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`)
    }
  })

  useEffect(() => {
    let active = true
    void Promise.resolve().then(() => {
      if (active) initializeProject()
    })
    return () => { active = false }
  }, [jobId])

  const pendingRender = titles.some((title) => title.status === 'RENDERING' || (title.status === 'PENDING' && queuedIds.includes(title.id)))
  const pollTitles = useEffectEvent(() => { void loadTitles(true) })
  useEffect(() => {
    if (!pendingRender) return
    const timer = window.setInterval(pollTitles, 7000)
    return () => window.clearInterval(timer)
  }, [pendingRender, jobId])

  const loading = loadedJobId !== jobId
  const availableTitles = titles.filter((title) => !isUnavailable(title, queuedIds) && title.status !== 'FAILED')
  const readyCount = titles.filter((title) => title.status === 'READY').length

  function toggleTitle(titleId: string) {
    setSelected((current) => current.includes(titleId) ? current.filter((id) => id !== titleId) : [...current, titleId])
  }

  async function handleQueue(ids = selected) {
    if (!ids.length || queueing) return
    setQueueing(true)
    setError('')
    try {
      const idsToQueue = [...ids]
      await queueClips(jobId, idsToQueue)
      markTitlesQueued(jobId, idsToQueue)
      setQueuedIds(getQueuedTitleIds(jobId))
      setSelected([])
      setNotice(`${idsToQueue.length} ${idsToQueue.length === 1 ? 'clip is' : 'clips are'} now in the render queue.`)
      await loadTitles(true)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not queue the selected clips.')
    } finally {
      setQueueing(false)
    }
  }

  function openChoice() {
    if (!selected.length) return
    setChoiceIds([...selected])
    setChoiceOpen(true)
  }

  async function handleUseDefault() {
    setChoiceOpen(false)
    await handleQueue(choiceIds)
  }

  function handleEdit() {
    setChoiceOpen(false)
    if (!choiceIds.length) return
    navigate(`/projects/${encodeURIComponent(jobId)}/clip-style?titles=${encodeURIComponent(choiceIds.join(','))}`)
  }

  const projectName = getStoredProjects().find((item) => item.id === jobId)?.filename || `Project ${jobId.slice(0, 8)}`

  return (
    <div className="page-content">
      <Link className="back-link" to="/"><ArrowLeft size={16} /> All projects</Link>
      <header className="project-heading">
        <div className="project-title-block"><span className="project-heading-icon"><Film size={20} /></span><div><p className="eyebrow">PROJECT / {jobId.slice(0, 8).toUpperCase()}</p><h1>{projectName}</h1><p className="heading-subtitle">Select the moments you want to turn into video clips.</p></div></div>
        <div className="project-heading-actions">
          <Link className="secondary-button refresh-button" to={`/projects/${encodeURIComponent(jobId)}/clips`}>
            <Sparkles size={15} /> View project clips
          </Link>
          <button className="secondary-button refresh-button" type="button" onClick={() => void loadTitles(true)} disabled={refreshing}><RefreshCw size={16} className={refreshing ? 'spin' : ''} /> Refresh titles</button>
        </div>
      </header>
      {notice && <div className="success-banner"><span><Check size={16} /></span>{notice}<button type="button" onClick={() => setNotice('')} aria-label="Dismiss message">×</button></div>}
      {error && <div className="inline-error" role="alert"><CircleAlert size={16} />{error}</div>}
      <section className="title-section">
        <div className="title-section-head"><div><p className="eyebrow">STORY CATALOG</p><h2>Recommended titles <span>{titles.length}</span></h2><p className="muted-copy">Choose one or more titles to queue their clips.</p></div>{titles.length > 0 && <button className="text-button select-available" type="button" onClick={() => setSelected(availableTitles.map((title) => title.id))} disabled={!availableTitles.length}>Select available</button>}</div>
        {loading ? <div className="loading-state"><span className="loader" />Loading titles</div> : titles.length ? <>
          <div className="title-table-head"><span>Title</span><span>Source time</span><span>Clip status</span><span>Output</span></div>
          <div className="title-list">
            {titles.map((title, index) => {
              const unavailable = isUnavailable(title, queuedIds)
              const failed = title.status === 'FAILED'
              const state = renderState(title.status, queuedIds.includes(title.id))
              return <article className={`title-row${unavailable ? ' title-disabled' : ''}`} key={title.id}>
                <label className="title-choice"><input type="checkbox" checked={selected.includes(title.id)} disabled={unavailable} onChange={() => toggleTitle(title.id)} aria-label={`Select ${title.title}`} /><span className={`title-number tone-${index % 4}`}><Film size={16} /></span><span className="title-name"><strong>{title.title}</strong><small>{Number(title.duration).toFixed(1)} sec · {title.id.slice(0, 12)}</small></span></label>
                <span className="source-time"><Clock3 size={14} />{seconds(title.start_time)} – {seconds(title.end_time)}</span>
                <span className={`clip-state state-${state.tone}`}><i />{failed ? 'Retry available' : state.label}</span>
                <span className="title-output">{title.download_url ? <a href={title.download_url} target="_blank" rel="noreferrer" className="download-link" aria-label={`Download ${title.title}`} title="Download clip"><Download size={16} /></a> : unavailable ? <span className="output-pending"><Clock3 size={15} /></span> : <span className="output-empty">—</span>}</span>
              </article>
            })}
          </div>
          <div className="queue-footer"><div><Sparkles size={16} /><span>{selected.length ? `${selected.length} title${selected.length === 1 ? '' : 's'} selected` : `${readyCount} of ${titles.length} clips ready`}</span></div><button className="primary-button" type="button" onClick={openChoice} disabled={!selected.length || queueing}>{queueing ? 'Adding to queue…' : 'Generate selected clips'} {!queueing && <Scissors size={16} />}</button></div>
        </> : <div className="empty-state title-empty"><span className="empty-icon"><Film size={22} /></span><h2>Titles are on the way</h2><p>Refresh this page after analysis completes. Your recommended moments will appear here.</p><button className="secondary-button" type="button" onClick={() => void loadTitles(true)}><RefreshCw size={15} /> Check again</button></div>}
      </section>
      {titles.some((title) => title.status === 'FAILED') && <p className="retry-note"><ArrowUpRight size={14} /> Failed renders can be selected and queued again.</p>}
      <SubtitleChoiceModal open={choiceOpen} count={choiceIds.length} onUseDefault={() => void handleUseDefault()} onEdit={handleEdit} onClose={() => setChoiceOpen(false)} />
    </div>
  )
}