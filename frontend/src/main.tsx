import { MotionConfig } from 'motion/react'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.tsx'
import { Announcer } from './components/Announcer'
import { DesignRoute } from './design/DesignRoute'
import { ToastProvider } from './ui'
import './index.css'

const root = document.getElementById('root')
if (!root) throw new Error('#root element missing from index.html')

const design = window.location.pathname.replace(/\/+$/, '') === '/design'

createRoot(root).render(
  <StrictMode>
    <MotionConfig reducedMotion="user">
      <ToastProvider>
        <Announcer>{design ? <DesignRoute /> : <App />}</Announcer>
      </ToastProvider>
    </MotionConfig>
  </StrictMode>,
)
