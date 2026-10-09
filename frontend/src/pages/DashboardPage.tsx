import { ArrowUpRight, Clapperboard, Clock3, Film, FolderPlus, RefreshCw, Search, TriangleAlert } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ApiError, listAllTitles } from '../lib/api'
import { getStoredProjects } from '../lib/localProjects'
import type { JobStatus, StoredJob, Title } from '../types'

interface ProjectSummary {
  id: string
  name: string
  status: JobStatus
  createdAt: string
  titleCount: number
}

type FilterValue = 'ALL' | JobStatus

const statusLabels: Record<JobStatus, string> = {
  PENDING: 'Queued',
  PROCESSING: 'Processing',
  COMPLETED: 'Titles ready',
  FAILED: 'Needs attention',
}

function buildProjects(stored: StoredJob[], titles: Title[]): ProjectSummary[] {
  const grouped = new Map<string, Title[]>()
  for (const title of titles) grouped.set(title.job_id, [...(grouped.get(title.job_id) || []), title])
  const storedById = new Map(stored.map((project) => [project.id, project]))
  const ids = new Set([...storedById.keys(), ...grouped.keys()])
  return [...ids].map((id) => {
    const local = storedById.get(id)
    const projectTitles = grouped.get(id) || []
    return {
      id,
      name: local?.filename || `Project ${id.slice(0, 8)}`,
      status: projectTitles.length ? 'COMPLETED' : local?.status || 'PENDING',
      createdAt: local?.created_at || projectTitles[0]?.created_at || new Date().toISOString(),
      titleCount: projectTitles.length,
    }
  }).sort((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt))
}

function StatusPill({ status }: { status: JobStatus }) {
  return <span className={`status-pill status-${status.toLowerCase()}`}><span className="status-dot" />{statusLabels[status]}</span>
}

export function DashboardPage() {
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [filter, setFilter] = useState<FilterValue>('ALL')
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')

  async function refreshProjects() {
    setRefreshing(true)
    setError('')
    try {
      const result = await listAllTitles()
      setProjects(buildProjects(getStoredProjects(), result.items))
    } catch (reason) {
      setProjects(buildProjects(getStoredProjects(), []))
      setError(reason instanceof ApiError ? reason.message : 'Could not load project titles.')
    } finally {
      setRefreshing(false)
    }
  }

  useEffect(() => {
    let cancelled = false
    void listAllTitles().then((result) => {
      if (!cancelled) setProjects(buildProjects(getStoredProjects(), result.items))
    }).catch((reason: unknown) => {
      if (cancelled) return
      setProjects(buildProjects(getStoredProjects(), []))
      setError(reason instanceof ApiError ? reason.message : 'Could not load project titles.')
    }).finally(() => {
      if (!cancelled) setLoading(false)
    })
    return () => { cancelled = true }
  }, [])

  const filteredProjects = projects.filter((project) => {
    const matchesStatus = filter === 'ALL' || project.status === filter
    return matchesStatus && project.name.toLowerCase().includes(search.toLowerCase())
  })
  const completedCount = projects.filter((project) => project.status === 'COMPLETED').length
  const activeCount = projects.filter((project) => project.status === 'PENDING' || project.status === 'PROCESSING').length
  const filters: { label: string; value: FilterValue }[] = [
    { label: 'All projects', value: 'ALL' },
    { label: 'In progress', value: 'PROCESSING' },
    { label: 'Ready', value: 'COMPLETED' },
    { label: 'Needs attention', value: 'FAILED' },
  ]

  return (
    <div className="page-content">
      <header className="page-heading">
        <div><p className="eyebrow">WORKSPACE / PROJECTS</p><h1>Your projects</h1><p className="heading-subtitle">Every source video, with its stories in one place.</p></div>
        <Link className="primary-button" to="/upload"><FolderPlus size={17} /> Create project</Link>
      </header>
      <section className="stats-band" aria-label="Project overview">
        <div className="stat-item"><span className="stat-icon green"><Clapperboard size={17} /></span><div><strong>{projects.length}</strong><span>Total projects</span></div></div>
        <div className="stat-item"><span className="stat-icon coral"><Clock3 size={17} /></span><div><strong>{activeCount}</strong><span>In progress</span></div></div>
        <div className="stat-item"><span className="stat-icon blue"><Film size={17} /></span><div><strong>{completedCount}</strong><span>Titles ready</span></div></div>
      </section>
      <div className="notice-line"><TriangleAlert size={16} /><span>The API does not provide a jobs list yet. This view combines uploads saved in this browser with jobs that already have titles.</span></div>
      <section className="projects-section">
        <div className="section-toolbar">
          <div className="filter-tabs" role="tablist" aria-label="Filter projects">
            {filters.map((item) => <button key={item.value} type="button" role="tab" aria-selected={filter === item.value} className={`filter-tab${filter === item.value ? ' selected' : ''}`} onClick={() => setFilter(item.value)}>{item.label}</button>)}
          </div>
          <div className="list-actions">
            <label className="search-field"><Search size={16} /><input aria-label="Search projects" placeholder="Search projects" value={search} onChange={(event) => setSearch(event.target.value)} /></label>
            <button className="icon-button" type="button" onClick={() => void refreshProjects()} disabled={refreshing} aria-label="Refresh projects" title="Refresh projects"><RefreshCw size={16} className={refreshing ? 'spin' : ''} /></button>
          </div>
        </div>
        {error && <div className="inline-error" role="alert">{error}</div>}
        {loading ? <div className="loading-state"><span className="loader" />Loading projects</div> : filteredProjects.length ? (
          <div className="project-list">
            {filteredProjects.map((project, index) => (
              <Link className="project-row" to={`/projects/${encodeURIComponent(project.id)}`} key={project.id}>
                <span className={`project-art art-${index % 4}`}><Film size={21} /><small>{String(index + 1).padStart(2, '0')}</small></span>
                <span className="project-info"><strong>{project.name}</strong><span>{new Date(project.createdAt).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })} <i /> {project.titleCount} {project.titleCount === 1 ? 'title' : 'titles'}</span></span>
                <StatusPill status={project.status} />
                <span className="row-arrow"><ArrowUpRight size={17} /></span>
              </Link>
            ))}
          </div>
        ) : (
          <div className="empty-state"><span className="empty-icon"><Film size={22} /></span><h2>{projects.length ? 'No matching projects' : 'Start with a source video'}</h2><p>{projects.length ? 'Try another search or status filter.' : 'Upload an MP4 and Cutline will find the moments worth turning into clips.'}</p>{!projects.length && <Link className="secondary-button" to="/upload"><FolderPlus size={16} /> Create your first project</Link>}</div>
        )}
      </section>
    </div>
  )
}