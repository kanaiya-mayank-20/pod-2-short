import { Clapperboard, FolderKanban, LogOut, Plus } from 'lucide-react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/useAuth'

export function AppShell() {
  const { signOut } = useAuth()
  const navigate = useNavigate()

  function handleSignOut() {
    signOut()
    navigate('/login', { replace: true })
  }

  return (
    <div className="app-frame">
      <aside className="sidebar">
        <NavLink to="/" className="brand" aria-label="Cutline Studio home">
          <span className="brand-mark"><Clapperboard size={19} strokeWidth={2.1} /></span>
          <span>cutline<span className="brand-light"> studio</span></span>
        </NavLink>
        <div className="workspace-label">WORKSPACE</div>
        <nav className="side-nav" aria-label="Main navigation">
          <NavLink to="/" end className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}><FolderKanban size={17} /><span>Projects</span></NavLink>
          <NavLink to="/upload" className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}><Plus size={17} /><span>New project</span></NavLink>
        </nav>
        <div className="sidebar-bottom">
          <div className="user-chip"><span className="avatar">CS</span><span className="user-meta"><strong>Studio account</strong><small>Video workspace</small></span></div>
          <button className="signout-button" onClick={handleSignOut} type="button"><LogOut size={16} /><span>Sign out</span></button>
        </div>
      </aside>
      <main className="main-pane"><Outlet /></main>
    </div>
  )
}