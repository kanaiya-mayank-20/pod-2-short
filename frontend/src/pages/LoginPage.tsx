import { ArrowUpRight, Clapperboard, Eye, EyeOff, LockKeyhole, Mail } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { Link } from 'react-router-dom'
import { useAuth } from '../auth/useAuth'

export function LoginPage() {
  const { signIn } = useAuth()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError('')
    setSubmitting(true)
    try {
      await signIn(email.trim(), password)
      navigate('/', { replace: true })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Sign in failed. Please try again.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="login-page">
      <section className="login-story" aria-label="Cutline Studio">
        <div className="login-brand"><span className="brand-mark"><Clapperboard size={19} /></span>cutline<span> studio</span></div>
        <div className="story-copy">
          <p className="eyebrow light">YOUR VIDEO WORKSPACE</p>
          <h1>Find the moments<br />worth keeping.</h1>
          <p className="story-description">Turn long-form footage into a clear set of stories, ready to shape into clips.</p>
        </div>
        <div className="film-strip" aria-hidden="true">
          <div className="film-frame frame-one"><span>01</span></div>
          <div className="film-frame frame-two"><span>02</span></div>
          <div className="film-frame frame-three"><span>03</span></div>
          <div className="film-caption"><span className="live-dot" /> A little more signal, a lot less searching</div>
        </div>
        <span className="story-footnote">CUTLINE STUDIO · VIDEO INTELLIGENCE</span>
      </section>
      <section className="login-panel">
        <div className="login-panel-top"><span>Welcome back</span><span className="secure-label"><LockKeyhole size={13} /> SECURE SIGN IN</span></div>
        <div className="login-form-wrap">
          <p className="eyebrow">ACCOUNT ACCESS</p>
          <h2>Sign in to your<br />workspace.</h2>
          <p className="muted-copy">Use the email and password linked to your account.</p>
          <form className="login-form" onSubmit={handleSubmit}>
            <label htmlFor="email">Email address</label>
            <div className="field-with-icon"><Mail size={17} /><input id="email" type="email" autoComplete="username" placeholder="you@company.com" value={email} onChange={(event) => setEmail(event.target.value)} required /></div>
            <label htmlFor="password">Password</label>
            <div className="field-with-icon"><LockKeyhole size={17} /><input id="password" type={showPassword ? 'text' : 'password'} autoComplete="current-password" placeholder="Enter your password" value={password} onChange={(event) => setPassword(event.target.value)} required /><button className="password-toggle" type="button" onClick={() => setShowPassword((value) => !value)} aria-label={showPassword ? 'Hide password' : 'Show password'}>{showPassword ? <EyeOff size={17} /> : <Eye size={17} />}</button></div>
            {error && <p className="form-error" role="alert">{error}</p>}
            <button className="primary-button login-submit" type="submit" disabled={submitting}>{submitting ? 'Signing in…' : 'Sign in'} <ArrowUpRight size={17} /></button>
          </form>
          <p className="account-note">New to Cutline? <Link to="/register">Create an account</Link></p>
        </div>
        <div className="login-panel-footer"><span>© Cutline Studio</span><span>Private workspace</span></div>
      </section>
    </main>
  )
}