import { createRoot } from 'react-dom/client'
import { ThemeProvider } from './components/web/theme-provider'
import { BrowserRouter } from 'react-router-dom'
import { TooltipProvider } from '@/components/ui/tooltip'
import './index.css'
import App from './App.jsx'

createRoot(document.getElementById('root')).render(
  <ThemeProvider defaultTheme="system" storageKey="vite-ui-theme">
    <BrowserRouter>
      <TooltipProvider>
        <App />
      </TooltipProvider>
    </BrowserRouter>
  </ThemeProvider>
)