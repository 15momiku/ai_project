import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import NavBar from './components/NavBar'
import Toast from './components/Toast'
import { useAuth } from './auth/AuthContext'
import Home from './pages/Home'
import JobHub from './pages/JobHub'
import Setup from './pages/Setup'
import Interview from './pages/Interview'
import Report from './pages/Report'
import MyInterviews from './pages/MyInterviews'
import Leaderboard from './pages/Leaderboard'
import ResumeOptimize from './pages/ResumeOptimize'
import Login from './pages/Login'

/**
 * 需要登录才能进的页面守卫。
 *
 * 默认 `ALLOW_GUEST=true`（游客可试用），此时守卫直接放行；
 * 只有后端把 `ALLOW_GUEST` 设为 false（数据完全私有）时才会拦到登录页，
 * 并记下来路，登录后原地返回。
 */
function Guard({ children }) {
  const { loading, allowGuest, isGuest } = useAuth()
  const location = useLocation()
  if (loading) return <div className="container center mt-24"><span className="spinner" /></div>
  if (!allowGuest && isGuest) {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
  }
  return children
}

export default function App() {
  const { pathname } = useLocation()
  // 面试页保持沉浸；登录页也去掉导航，让表单成为唯一焦点
  const immersive = pathname.startsWith('/interview/') || pathname.startsWith('/login')

  return (
    <div className="app-shell">
      <div className="app-bg" aria-hidden="true" />
      {!immersive && <NavBar />}
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/" element={<Guard><Home /></Guard>} />
        {/* 岗位信息汇总：入口紧跟首页（先确定投什么岗，再针对目标岗位练面试）。
            整页要求登录：本项目的游客走进程内内存库，岗位库在那里是空的，
            放行只会得到一个「岗位库是空的」假象。 */}
        <Route path="/jobs" element={<Guard><JobHub /></Guard>} />
        <Route path="/setup" element={<Guard><Setup /></Guard>} />
        {/* 简历优化：入口紧跟模拟面试（与导航顺序同源） */}
        <Route path="/resume-optimizer" element={<Guard><ResumeOptimize /></Guard>} />
        <Route path="/interview/:sessionId" element={<Guard><Interview /></Guard>} />
        <Route path="/report/:sessionId" element={<Guard><Report /></Guard>} />
        {/* 我的面试：报告 / 错题本 / 录音回放三个子标签（游客模式页内提示「仅登录可见」） */}
        <Route path="/my-interviews" element={<Guard><MyInterviews /></Guard>} />
        {/* 旧地址保留为跳转：错题本已成为「我的面试」的子标签 */}
        <Route path="/wrongbook" element={<Navigate to="/my-interviews?tab=wrongbook" replace />} />
        <Route path="/leaderboard" element={<Guard><Leaderboard /></Guard>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <Toast />
    </div>
  )
}
