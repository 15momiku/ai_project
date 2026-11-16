import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import AppErrorBoundary from './components/AppErrorBoundary'
import { AuthProvider } from './auth/AuthContext'
import { initTheme } from './theme'
import './styles/global.css'

// 体验增强 §13：先落主题再渲染，避免深色用户看到一帧白屏。
// index.html 里已有一段等价内联脚本兜住首帧，这里负责"跟随系统"的后续变化。
initTheme()

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    {/* 最外层错误边界：渲染期异常不再让整棵树被卸载成白屏（详见组件注释） */}
    <AppErrorBoundary>
      <BrowserRouter>
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>
    </AppErrorBoundary>
  </React.StrictMode>
)
