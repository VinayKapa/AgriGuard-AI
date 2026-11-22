import { useState } from 'react'
import { Sprout, ShieldCheck } from 'lucide-react'

const API = import.meta.env.VITE_API_URL || 'http://localhost:8000'

export default function Auth({ onSignedIn }) {
  const [mode, setMode] = useState('login')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function submit(event) {
    event.preventDefault(); setBusy(true); setError('')
    try {
      const response = await fetch(`${API}/api/auth/${mode === 'login' ? 'login' : 'register'}`, {
        method: 'POST', credentials: 'include', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(mode === 'login' ? {email, password} : {name, email, password}),
      })
      const result = await response.json()
      if (!response.ok) throw new Error(result.detail || 'Could not sign in.')
      onSignedIn(result.user)
    } catch (e) { setError(e.message || 'Could not reach AgriGuard. Is the backend running?') }
    finally { setBusy(false) }
  }

  return <main className="auth-page"><section className="auth-card"><div className="auth-brand"><div className="brand-mark"><Sprout size={21}/></div><span>agri<span className="brand-light">guard</span><small>FIELD INTELLIGENCE</small></span></div><div className="auth-intro"><div className="eyebrow"><span/> YOUR FARM, YOUR DATA</div><h1>{mode === 'login' ? 'Welcome back.' : 'Start with your farm.'}</h1><p>{mode === 'login' ? 'Sign in to see live conditions and your saved field outlook.' : 'Create an account to save your farm and get location based outlooks.'}</p></div><form onSubmit={submit}>{mode === 'register'&&<label>Your name<input autoComplete="name" required minLength="2" value={name} onChange={e=>setName(e.target.value)} placeholder="Name"/></label>}<label>Email<input type="email" autoComplete="email" required value={email} onChange={e=>setEmail(e.target.value)} placeholder="you@example.com"/></label><label>Password<input type="password" autoComplete={mode==='login'?'current-password':'new-password'} required minLength="8" value={password} onChange={e=>setPassword(e.target.value)} placeholder="At least 8 characters"/></label>{error&&<div className="auth-error">{error}</div>}<button className="auth-submit" disabled={busy}>{busy?'Please wait…':mode==='login'?'Sign in':'Create account'}</button></form><div className="auth-switch">{mode==='login'?"New to AgriGuard?":"Already have an account?"}<button onClick={()=>{setMode(mode==='login'?'register':'login');setError('')}}>{mode==='login'?'Create account':'Sign in'}</button></div><div className="auth-privacy"><ShieldCheck size={14}/> Your password is securely hashed. Farm settings are private to your account.</div></section><div className="auth-art"><div className="auth-sun"/><div className="auth-hill auth-hill-a"/><div className="auth-hill auth-hill-b"/><div className="auth-copy"><span>FARM INTELLIGENCE THAT MOVES WITH YOU</span><h2>Know your field.<br/><em>Plan with confidence.</em></h2><p>Live weather for your location, clear risk signals, and practical next steps.</p></div></div></main>
}
