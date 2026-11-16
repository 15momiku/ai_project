import { useEffect, useState } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import {
  IconHome,
  IconMic,
  IconBook,
  IconTrophy,
  IconUser,
  IconFilePen,
  IconBulb,
  IconLock,
  IconBriefcase,
} from './icons'
import { THEME_OPTIONS, applyTheme, readThemePref, setThemePref } from '../theme'

const LINKS = [
  { to: '/', label: '首页', Icon: IconHome, exact: true },
  // 「岗位信息汇总」紧跟首页：
  // 动线是「先看有哪些岗、投到哪一步了，再针对目标岗位练面试」——
  // 模拟面试的目标岗位本来就来自这份汇总表。
  // 移动端底栏已经很挤，因此这一格用两字短标签（`short`），桌面用全称。
  { to: '/jobs', label: '岗位信息汇总', short: '岗位', Icon: IconBriefcase, loginOnly: true },
  // 「模拟面试 / 简历优化 / 我的面试」仅登录用户可用：游客模式下带锁标记，
  // 点进去是「仅登录用户可见」的说明卡（后端这些接口同样返回 401，不只是前端拦截）。
  // 排行榜保持公开：它不产生也不暴露个人数据，是产品对外的展示面。
  { to: '/setup', label: '模拟面试', Icon: IconMic, loginOnly: true },
  { to: '/resume-optimizer', label: '简历优化', Icon: IconFilePen, loginOnly: true },
  { to: '/my-interviews', label: '我的面试', Icon: IconBook, loginOnly: true },
  { to: '/leaderboard', label: '排行榜', Icon: IconTrophy },
]

/**
 * 导航：桌面端顶部导航条 / 移动端底部固定 tab（当前路由高亮）
 * 面试进行中（/interview/:id）由 App 控制不渲染，保持沉浸。
 * 右侧（移动端为最后一个 tab）显示登录入口或当前账号 + 退出。
 *
 * 顺序：首页 → 岗位信息汇总 → 模拟面试 → 简历优化 → 我的面试 → 排行榜。
 * 「岗位信息汇总」排在首页之后、模拟面试之前：先确定投什么岗，再针对它练面试。
 * 「简历优化」排在模拟面试之后：先跑通一场面试、拿到六维报告知道差在哪，
 * 再回头改简历，动线更顺（README 的「导航顺序」与此保持一致）。
 * 「我的面试」把报告 / 错题本 / 录音回放收进一个入口（错题本因此不再单独占一格，
 * 移动端底部 tab 维持 5 个功能项）。
 *
 * `loginOnly` 的项在游客模式下带锁标记：点进去会看到「仅登录用户可见」的说明卡，
 * 而不是一路走到一个空页面（游客的数据本来就不存在）。
 *
 * 图标走 components/icons.jsx 的统一矢量字汇（与页面内图标同源），
 * 避免各处手写 SVG 导致线宽/比例不一致。
 */
export default function NavBar() {
  const { pathname } = useLocation()
  const nav = useNavigate()
  const { user, isGuest, logout } = useAuth()
  const isOn = (link) => (link.exact ? pathname === '/' : pathname.startsWith(link.to))

  /* 体验增强 §13：三态主题偏好（跟随系统 / 浅色 / 深色），与 index.html 的内联脚本同源 */
  const [themePref, setPref] = useState(() => readThemePref())
  useEffect(() => {
    // 系统主题变化时（system 模式下）刷新一下实际生效值，保持按钮状态与页面对应
    const off = applyTheme(readThemePref())
    const mq = typeof window !== 'undefined' && window.matchMedia
      ? window.matchMedia('(prefers-color-scheme: dark)')
      : null
    const onChange = () => {
      if (readThemePref() === 'system') applyTheme('system')
    }
    if (mq) {
      if (mq.addEventListener) mq.addEventListener('change', onChange)
      else if (mq.addListener) mq.addListener(onChange)
    }
    return () => {
      if (!mq) return
      if (mq.removeEventListener) mq.removeEventListener('change', onChange)
      else if (mq.removeListener) mq.removeListener(onChange)
    }
  }, [])
  const cycleTheme = () => {
    const list = THEME_OPTIONS.map((o) => o.key)
    const next = list[(list.indexOf(themePref) + 1) % list.length]
    setPref(next)
    setThemePref(next)
  }
  const themeLabel = THEME_OPTIONS.find((o) => o.key === themePref)?.label || '跟随系统'

  const nickname = user?.nickname || user?.username || '同学'

  const onLogout = async () => {
    if (!window.confirm('确定要退出登录吗？退出后本机将以游客身份继续浏览。')) return
    await logout()
    nav('/', { replace: true })
  }

  return (
    <>
      {/* 移动端底部 Tab */}
      <nav className="nav-bottom">
        {LINKS.map((l) => (
          <NavLink
            key={l.to}
            to={l.to}
            className={isOn(l) ? 'on' : ''}
            title={l.loginOnly && isGuest ? `${l.label}（仅登录用户可见）` : undefined}
          >
            <l.Icon size={21} />
            <span>
              {l.short || l.label}
              {l.loginOnly && isGuest && (
                <IconLock size={11} style={{ marginLeft: 3, verticalAlign: '-1px', opacity: 0.75 }} />
              )}
            </span>
          </NavLink>
        ))}
        {isGuest ? (
          <NavLink to="/login" className={pathname.startsWith('/login') ? 'on' : ''}>
            <IconUser size={21} />
            <span>登录</span>
          </NavLink>
        ) : (
          <button type="button" className="nav-account-btn" onClick={onLogout} title="退出登录">
            <IconUser size={21} />
            <span>退出</span>
          </button>
        )}
      </nav>

      {/* 移动端主题切换：底部 tab 已经占满一行（5 个功能 + 登录/退出），
          再塞一项会挤，因此做成不与 tab 同行的浮动按钮 */}
      <button
        type="button"
        className="nav-theme-fab"
        onClick={cycleTheme}
        title={`主题：${themeLabel}（点击切换）`}
        aria-label={`切换主题，当前为${themeLabel}`}
      >
        <IconBulb size={16} />
      </button>

      {/* 桌面端顶部导航 */}
      <nav className="nav-top">
        <div className="nav-top-inner">
          <div className="nav-brand">
            <span className="logo">校</span>
            <span>
              校招全流程助手
              <span className="mute-2 hide-sm" style={{ fontSize: 12, fontWeight: 500, marginLeft: 8 }}>
                找岗 · 改简历 · 练面试 · 复盘
              </span>
            </span>
          </div>
          <div className="nav-links">
            {LINKS.map((l) => (
              <NavLink
                key={l.to}
                to={l.to}
                className={isOn(l) ? 'on' : ''}
                title={l.loginOnly && isGuest ? '仅登录用户可见' : undefined}
              >
                {l.label}
                {l.loginOnly && isGuest && (
                  <IconLock size={12} style={{ marginLeft: 4, verticalAlign: '-1px', opacity: 0.7 }} />
                )}
              </NavLink>
            ))}
          </div>
          <div className="nav-user">
            {/* 主题切换：三态循环（跟随系统 → 浅色 → 深色），不使用图标库，用现有矢量图标 + 文字 */}
            <button
              type="button"
              className="link-btn nav-theme"
              onClick={cycleTheme}
              title={`主题：${themeLabel}（点击切换）`}
              aria-label={`切换主题，当前为${themeLabel}`}
            >
              <IconBulb size={15} />
              <span className="hide-sm">{themeLabel}</span>
            </button>
            {isGuest ? (
              <NavLink to="/login" className="btn btn--primary btn--sm nav-login-btn">
                登录 / 注册
              </NavLink>
            ) : (
              <>
                <span className="nav-user-name" title={nickname}>
                  {nickname}
                </span>
                <button type="button" className="link-btn" onClick={onLogout}>
                  退出
                </button>
              </>
            )}
          </div>
        </div>
      </nav>
    </>
  )
}
