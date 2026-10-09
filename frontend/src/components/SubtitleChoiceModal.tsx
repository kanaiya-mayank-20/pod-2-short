import { Scissors, SlidersHorizontal, X } from 'lucide-react'

type Props = {
  open: boolean
  count: number
  onUseDefault: () => void
  onEdit: () => void
  onClose: () => void
}

export function SubtitleChoiceModal({ open, count, onUseDefault, onEdit, onClose }: Props) {
  if (!open) return null

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-label="Choose how subtitles are styled">
      <div className="modal-card">
        <button className="modal-close" type="button" onClick={onClose} aria-label="Close">
          <X size={17} />
        </button>
        <h2 className="modal-title">Style the subtitles of {count} {count === 1 ? 'title' : 'titles'}?</h2>
        <p className="modal-copy">
          Every clip is burned with subtitles in the default look below. Generate them as they are, or open the
          editor to tune the style first.
        </p>
        <div className="demo-stage">
          <video className="demo-video" src="/demo/default-subtitles.mp4" muted loop autoPlay playsInline />
          <span className="demo-caption">Default subtitle style</span>
        </div>
        <div className="modal-actions">
          <button className="primary-button" type="button" onClick={onUseDefault}>
            <Scissors size={15} /> Use default style
          </button>
          <button className="secondary-button" type="button" onClick={onEdit}>
            <SlidersHorizontal size={15} /> Edit subtitles
          </button>
        </div>
      </div>
    </div>
  )
}