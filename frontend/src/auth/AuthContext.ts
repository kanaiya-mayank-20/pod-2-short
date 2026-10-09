import { createContext } from 'react'

export interface AuthContextValue {
  accessToken: string | null
  signIn: (email: string, password: string) => Promise<void>
  signOut: () => void
}

export const AuthContext = createContext<AuthContextValue | null>(null)