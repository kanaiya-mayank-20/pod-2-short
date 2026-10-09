import { useEffect, useState, type ReactNode } from 'react'
import { login } from '../lib/api'
import { AuthContext } from './AuthContext'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [accessToken, setAccessToken] = useState(() => sessionStorage.getItem('cutline.accessToken'))

  function signOut() {
    sessionStorage.removeItem('cutline.accessToken')
    setAccessToken(null)
  }

  useEffect(() => {
    window.addEventListener('cutline:auth-expired', signOut)
    return () => window.removeEventListener('cutline:auth-expired', signOut)
  }, [])

  async function signIn(email: string, password: string) {
    const token = await login(email, password)
    sessionStorage.setItem('cutline.accessToken', token)
    setAccessToken(token)
  }

  return <AuthContext.Provider value={{ accessToken, signIn, signOut }}>{children}</AuthContext.Provider>
}
