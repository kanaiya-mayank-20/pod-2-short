import { ArrowLeft, Film, Sparkles, Trash2, Wand2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ApiError, deleteClip, deleteClipVersion, listClipVersions, listJobTitles } from '../lib/api'
import type { ClipVersion, Title } from '../types'

export function JobClipsPage() {
  const { jobId = '' } = useParams()
  const [titles, setTitles] = useState<Title[]>([])
  const [versionsByTitle, setVersionsByTitle] = useState<Record<string, ClipVersion[]>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState<Record<string, boolean>>({})
  const pollTimeout = useRef<number | null>(null)

  async function loadProjectClips(showLoading = true) {
    if (showLoading) {
      setLoading(true)
      setError('')
    }
    try {
      const result = await listJobTitles(jobId)
      const grouped: Record<string, ClipVersion[]> = {}
      await Promise.all(
        result.items.map(async (title) => {
          const versions = await listClipVersions(jobId, title.id)
          grouped[title.id] = versions.items
        }),
      )
      setTitles(result.items)
      setVersionsByTitle(grouped)
      const rendering = result.items.some((title) => ['PENDING', 'RENDERING'].includes(title.status))
        || Object.values(grouped).some((versions) =>
          versions.some((version) => ['PENDING', 'RENDERING'].includes(version.status)),
        )
      if (pollTimeout.current !== null) window.clearTimeout(pollTimeout.current)
      pollTimeout.current = rendering
        ? window.setTimeout(() => void loadProjectClips(false), 2500)
        : null
    } catch (reason) {
      setError(reason instanceof ApiError && reason.status === 404 ? 'This project could not be found for your account.' : reason instanceof Error ? reason.message : 'Could not load project clips.')
    } finally {
      if (showLoading) setLoading(false)
    }
  }

  useEffect(() => {
    if (!jobId) return
    void loadProjectClips()
    return () => {
      if (pollTimeout.current !== null) window.clearTimeout(pollTimeout.current)
    }
  }, [jobId])

  async function handleDeleteClip(titleId: string) {
    if (!jobId) return
    const key = `clip-${titleId}`
    setBusy((current) => ({ ...current, [key]: true }))
    try {
      await deleteClip(jobId, titleId)
      await loadProjectClips()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not delete this clip.')
    } finally {
      setBusy((current) => ({ ...current, [key]: false }))
    }
  }

  async function handleDeleteVersion(titleId: string, versionId: string) {
    if (!jobId) return
    const key = `version-${versionId}`
    setBusy((current) => ({ ...current, [key]: true }))
    try {
      await deleteClipVersion(jobId, titleId, versionId)
      await loadProjectClips()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not delete this clip variation.')
    } finally {
      setBusy((current) => ({ ...current, [key]: false }))
    }
  }

  const projectName = titles[0]?.job_id ? `Project ${titles[0].job_id.slice(0, 8)}` : `Project ${jobId.slice(0, 8)}`

  return (
    <div className="page-content">
      <Link className="back-link" to={`/projects/${encodeURIComponent(jobId)}`}><ArrowLeft size={16} /> Back to titles</Link>
      <header className="project-heading">
        <div className="project-title-block"><span className="project-heading-icon"><Film size={20} /></span><div><p className="eyebrow">JOB CLIP LIBRARY</p><h1>{projectName}</h1><p className="heading-subtitle">Review every render and style variation produced for this project.</p></div></div>
        <Link className="secondary-button" to={`/projects/${encodeURIComponent(jobId)}`}><Sparkles size={15} /> Open title list</Link>
      </header>

      {error && <div className="inline-error" role="alert">{error}</div>}

      {loading ? (
        <div className="loading-state"><span className="loader" />Loading clips</div>
      ) : (
        <div className="clip-library-list">
          {titles.length === 0 ? (
            <div className="empty-state"><span className="empty-icon"><Film size={22} /></span><h2>No clips yet</h2><p>Generate clip variants from a title to start building your project library.</p></div>
          ) : titles.map((title) => {
            const versions = versionsByTitle[title.id] ?? []
            const hasStandardClip = title.status === 'READY' && !!title.download_url
            return (
              <article className="clip-library-card" key={title.id}>
                <div className="clip-card-header">
                  <div className="clip-card-title">
                    <strong>{title.title}</strong>
                    <small>{title.id.slice(0, 12)} · {title.start_time}s to {title.end_time}s</small>
                  </div>
                  <div className="clip-card-actions">
                    <Link className="secondary-button" to={`/projects/${encodeURIComponent(jobId)}/clip-style/${encodeURIComponent(title.id)}`}>
                      <Wand2 size={14} /> Style this title
                    </Link>
                  </div>
                </div>

                <div className="clip-output-grid">
                  <div className="clip-output-card">
                    <h4>Standard render</h4>
                    {hasStandardClip ? (
                      <>
                        <div className="clip-output-view"><em>{title.title}</em></div>
                        <div className="clip-output-meta">
                          <span>{title.status}</span>
                          <a href={title.download_url ?? '#'} target="_blank" rel="noreferrer">Download</a>
                        </div>
                        <button className="text-button" type="button" onClick={() => void handleDeleteClip(title.id)} disabled={busy[`clip-${title.id}`]}>
                          <Trash2 size={12} /> {busy[`clip-${title.id}`] ? 'Deleting…' : 'Delete clip'}
                        </button>
                      </>
                    ) : (
                      <>
                        <div className="clip-output-view"><em>{title.status}</em></div>
                        <p className="clip-empty">{title.error_message ?? (title.status === 'FAILED' ? 'The standard render failed.' : 'The standard render is still processing.')}</p>
                      </>
                    )}
                  </div>

                  {versions.length ? (
                    versions.map((version) => (
                      <div className="clip-output-card" key={version.id}>
                        <h4>{version.id.slice(0, 10)}</h4>
                        <div className="clip-output-view"><em>{version.title}</em></div>
                        <div className="clip-output-meta">
                          <span>{version.status}</span>
                          {version.download_url ? <a href={version.download_url} target="_blank" rel="noreferrer">Download styled clip</a> : <span>{version.status === 'FAILED' ? 'Failed' : 'Processing'}</span>}
                        </div>
                        {version.error_message && <p className="clip-empty">{version.error_message}</p>}
                        <button className="text-button" type="button" onClick={() => void handleDeleteVersion(title.id, version.id)} disabled={busy[`version-${version.id}`]}>
                          <Trash2 size={12} /> {busy[`version-${version.id}`] ? 'Deleting…' : 'Delete style'}
                        </button>
                      </div>
                    ))
                  ) : (
                    <div className="clip-output-card">
                      <h4>Style variations</h4>
                      <div className="clip-output-view"><em>Empty</em></div>
                      <p className="clip-empty">No styled clip variations for this title yet.</p>
                    </div>
                  )}
                </div>
              </article>
            )
          })}
        </div>
      )}
    </div>
  )
}
