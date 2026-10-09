import { ArrowLeft, Check, FileVideo2, Film, UploadCloud, X } from 'lucide-react'
import { useRef, useState, type ChangeEvent, type DragEvent, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { createUpload, putVideo } from '../lib/api'
import { saveProject } from '../lib/localProjects'

const maxSizeMb = Number(import.meta.env.VITE_MAX_VIDEO_SIZE_MB || 2048)
const maxSizeBytes = maxSizeMb * 1024 * 1024

export function UploadPage() {
  const inputRef = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()
  const [file, setFile] = useState<File | null>(null)
  const [dragging, setDragging] = useState(false)
  const [progress, setProgress] = useState(0)
  const [uploading, setUploading] = useState(false)
  const [stage, setStage] = useState('Preparing secure upload')
  const [error, setError] = useState('')

  function chooseFile(candidate?: File) {
    if (!candidate) return
    setError('')
    if (!candidate.name.toLowerCase().endsWith('.mp4')) {
      setFile(null)
      setError('Choose an MP4 video. Other formats are not supported by this workspace.')
      return
    }
    if (candidate.size > maxSizeBytes) {
      setFile(null)
      setError(`This video is larger than the configured ${maxSizeMb.toLocaleString()} MB limit.`)
      return
    }
    setFile(candidate)
  }

  function handleChange(event: ChangeEvent<HTMLInputElement>) {
    chooseFile(event.target.files?.[0])
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    setDragging(false)
    chooseFile(event.dataTransfer.files[0])
  }

  async function handleUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!file || uploading) return
    setUploading(true)
    setError('')
    setStage('Requesting a secure upload URL')
    try {
      const response = await createUpload(file)
      setStage('Uploading video to secure storage')
      await putVideo(file, response.upload, setProgress)
      saveProject({ id: response.job.id, filename: file.name, status: response.job.status, created_at: response.job.created_at })
      navigate(`/projects/${encodeURIComponent(response.job.id)}?uploaded=1`, { replace: true })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Upload failed. Please try again.')
      setUploading(false)
    }
  }

  return (
    <div className="page-content narrow-page">
      <Link className="back-link" to="/"><ArrowLeft size={16} /> Back to projects</Link>
      <header className="page-heading upload-heading"><div><p className="eyebrow">NEW PROJECT</p><h1>Bring in a video</h1><p className="heading-subtitle">Start with one source file. Your project is created as soon as its upload is complete.</p></div></header>
      <form className="upload-form" onSubmit={handleUpload}>
        <div className={`drop-zone${dragging ? ' dragging' : ''}${file ? ' has-file' : ''}`} onDragOver={(event) => { event.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)} onDrop={handleDrop}>
          <input ref={inputRef} className="visually-hidden" type="file" accept=".mp4,video/mp4" onChange={handleChange} />
          {file ? <>
            <span className="file-icon"><FileVideo2 size={24} /></span>
            <div className="file-selected"><strong>{file.name}</strong><span>{(file.size / (1024 * 1024)).toFixed(1)} MB · MP4 video</span></div>
            {!uploading && <button className="remove-file" type="button" aria-label="Remove selected video" onClick={() => { setFile(null); setProgress(0); if (inputRef.current) inputRef.current.value = '' }}><X size={17} /></button>}
          </> : <>
            <span className="upload-icon"><UploadCloud size={26} /></span>
            <strong>Drop your video here</strong>
            <span className="drop-help">MP4 files only · Up to {maxSizeMb.toLocaleString()} MB</span>
            <button className="browse-button" type="button" onClick={() => inputRef.current?.click()}>Browse files</button>
          </>}
        </div>
        {uploading && <div className="upload-progress"><div className="progress-heading"><span>{stage}</span><strong>{progress}%</strong></div><div className="progress-track"><span style={{ width: `${Math.max(progress, 4)}%` }} /></div><p>Your browser sends the file directly to secure storage. It does not pass through the API.</p></div>}
        {error && <div className="inline-error" role="alert">{error}</div>}
        <div className="upload-details"><div><span className="detail-icon"><Film size={17} /></span><span><strong>One source, one project</strong><small>The upload creates the video-processing job automatically.</small></span><Check size={16} className="detail-check" /></div><div><span className="detail-icon"><UploadCloud size={17} /></span><span><strong>Direct, signed upload</strong><small>Your file goes to the storage URL returned by the API.</small></span><Check size={16} className="detail-check" /></div></div>
        <div className="form-actions"><Link className="text-button" to="/">Cancel</Link><button className="primary-button" type="submit" disabled={!file || uploading}>{uploading ? 'Uploading…' : 'Upload video'} {!uploading && <UploadCloud size={17} />}</button></div>
      </form>
    </div>
  )
}