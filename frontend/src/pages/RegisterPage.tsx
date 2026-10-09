import { ArrowUpRight, Check, Clapperboard, LockKeyhole, Mail, UserRound } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { registerUser } from '../lib/api'

export function RegisterPage() {
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [created, setCreated] = useState(false)
  const formReady = Boolean(
    name.trim() &&
    email.trim() &&
    password.length >= 8 &&
    password.length <= 128 &&
    password === confirmPassword,
  )

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError('')
    if (password !== confirmPassword) {
      setError('Passwords do not match.')
      return
    }

    setSubmitting(true)
    try {
      await registerUser(name.trim(), email.trim(), password)
      setCreated(true)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not create your account. Please try again.')
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
          <h1>Make room for<br />the good parts.</h1>
          <p className="story-description">Create an account to organize source videos, explore recommended moments, and shape clips.</p>
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
        <div className="login-panel-top"><span>Create your account</span><span className="secure-label"><LockKeyhole size={13} /> SECURE SIGN UP</span></div>
        <div className="login-form-wrap register-form-wrap">
          {created ? <div className="registration-success" role="status">
            <span className="registration-success-icon"><Check size={23} /></span>
            <p className="eyebrow">ACCOUNT CREATED</p>
            <h2>You’re ready to<br />sign in.</h2>
            <p className="muted-copy">Your account has been created. Use your email and password to continue to your workspace.</p>
            <Link className="primary-button login-submit" to="/login">Continue to sign in <ArrowUpRight size={17} /></Link>
          </div> : <>
            <p className="eyebrow">NEW ACCOUNT</p>
            <h2>Join your<br />workspace.</h2>
            <p className="muted-copy">Create an account to get started with Cutline Studio.</p>
            <form className="login-form register-form" onSubmit={handleSubmit}>
              <label htmlFor="register-name">Full name</label>
              <div className="field-with-icon"><UserRound size={17} /><input id="register-name" type="text" autoComplete="name" placeholder="Your name" maxLength={200} value={name} onChange={(event) => setName(event.target.value)} required /></div>
              <label htmlFor="register-email">Email address</label>
              <div className="field-with-icon"><Mail size={17} /><input id="register-email" type="email" autoComplete="email" placeholder="you@company.com" maxLength={254} value={email} onChange={(event) => setEmail(event.target.value)} required /></div>
              <label htmlFor="register-password">Password</label>
              <div className="field-with-icon"><LockKeyhole size={17} /><input id="register-password" type="password" autoComplete="new-password" placeholder="At least 8 characters" minLength={8} maxLength={128} value={password} onChange={(event) => setPassword(event.target.value)} required /></div>
              <label htmlFor="register-confirm-password">Confirm password</label>
              <div className="field-with-icon"><LockKeyhole size={17} /><input id="register-confirm-password" type="password" autoComplete="new-password" placeholder="Re-enter your password" minLength={8} maxLength={128} value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} required /></div>
              {error && <p className="form-error" role="alert">{error}</p>}
              <button className="primary-button login-submit" type="submit" disabled={submitting || !formReady}>{submitting ? 'Creating account…' : 'Create account'} <ArrowUpRight size={17} /></button>
            </form>
            <p className="account-note">Already have an account? <Link to="/login">Sign in</Link></p>
          </>}
        </div>
        <div className="login-panel-footer"><span>© Cutline Studio</span><span>Private workspace</span></div>
      </section>
    </main>
  )
}