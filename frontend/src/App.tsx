import { Navigate, Outlet, Route, Routes } from 'react-router-dom'
import { useAuth } from './auth/useAuth'
import { AppShell } from './components/AppShell'
import { ClipStylePage } from './pages/ClipStylePage'
import { JobClipsPage } from './pages/JobClipsPage'
import { DashboardPage } from './pages/DashboardPage'
import { LoginPage } from './pages/LoginPage'
import { ProjectPage } from './pages/ProjectPage'
import { RegisterPage } from './pages/RegisterPage'
import { UploadPage } from './pages/UploadPage'

function RequireAuth() {
  const { accessToken } = useAuth()
  return accessToken ? <Outlet /> : <Navigate to="/login" replace />
}

function LoginRoute() {
  const { accessToken } = useAuth()
  return accessToken ? <Navigate to="/" replace /> : <LoginPage />
}

function RegisterRoute() {
  const { accessToken } = useAuth()
  return accessToken ? <Navigate to="/" replace /> : <RegisterPage />
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginRoute />} />
      <Route path="/register" element={<RegisterRoute />} />
      <Route element={<RequireAuth />}>
        <Route element={<AppShell />}>
          <Route index element={<DashboardPage />} />
          <Route path="upload" element={<UploadPage />} />
          <Route path="projects/:jobId" element={<ProjectPage />} />
          <Route path="projects/:jobId/clips" element={<JobClipsPage />} />
          <Route path="projects/:jobId/clip-style/:titleId?" element={<ClipStylePage />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
