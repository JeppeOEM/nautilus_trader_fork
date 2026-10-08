import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import CrashBoundary from './components/CrashBoundary'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <CrashBoundary area="app">
      <App />
    </CrashBoundary>
  </StrictMode>,
)
