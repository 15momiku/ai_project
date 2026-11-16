import { useEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { notify } from '../api/client'
import './login.css'

/* 与 backend/app/services/auth.py 的 USERNAME_RE / PASSWORD_MIN_LEN 保持一致 */
const USERNAME_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{2,31}$/
const PASSWORD_MIN = 6

const Icon = {
  eye: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  ),
  eyeOff: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M4 4l16 16" />
      <path d="M9.9 5.8A9.6 9.6 0 0 1 12 5.5c6 0 9.5 6.5 9.5 6.5a17 17 0 0 1-3.4 4.2" />
      <path d="M6.5 7.2A17 17 0 0 0 2.5 12S6 18.5 12 18.5c1 0 2-.2 2.8-.5" />
      <path d="M9.9 9.9a3 3 0 0 0 4.2 4.2" />
    </svg>
  ),
  lock: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="4.5" y="10.5" width="15" height="10" rx="2.5" />
      <path d="M8 10.5V8a4 4 0 0 1 8 0v2.5" />
    </svg>
  ),
  user: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="8.5" r="3.8" />
      <path d="M4.8 20a7.2 7.2 0 0 1 14.4 0" />
    </svg>
  ),
}

export default function Login() {
  const nav = useNavigate()
  const location = useLocation()
  const { login, register, isGuest, allowGuest, expiredNotice, clearExpiredNotice } = useAuth()

  const [tab, setTab] = useState('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [nickname, setNickname] = useState('')
  const [school, setSchool] = useState('')
  const [showPwd, setShowPwd] = useState(false)
  const [errors, setErrors] = useState({})
  const [serverError, setServerError] = useState('')
  const [busy, setBusy] = useState(false)

  const usernameRef = useRef(null)
  const passwordRef = useRef(null)

  // 跳转目标：优先 location.state.from（被拦截时记下的），否则 ?next=，最后回首页
  const next = location.state?.from || new URLSearchParams(location.search).get('next') || '/'

  // 已经是登录态（或被拦截后恢复了登录态）就不该停在登录页
  useEffect(() => {
    if (!isGuest) nav(next, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isGuest])

  useEffect(() => {
    usernameRef.current?.focus()
  }, [])

  const switchTab = (t) => {
    setTab(t)
    setErrors({})
    setServerError('')
    clearExpiredNotice()
    // 焦点移到第一个输入框，键盘用户不用重新 Tab 一遍
    requestAnimationFrame(() => usernameRef.current?.focus())
  }

  const validate = () => {
    const e = {}
    const name = username.trim()
    if (!name) e.username = '请输入账号'
    else if (tab === 'register' && !USERNAME_RE.test(name))
      e.username = '账号需 3-32 位，只能用字母、数字、下划线或连字符'
    if (!password) e.password = '请输入密码'
    else if (tab === 'register' && password.length < PASSWORD_MIN)
      e.password = `密码至少 ${PASSWORD_MIN} 位`
    setErrors(e)
    // 把焦点送到第一个出错的字段，避免用户找不到问题在哪
    if (e.username) usernameRef.current?.focus()
    else if (e.password) passwordRef.current?.focus()
    return Object.keys(e).length === 0
  }

  const onSubmit = async (ev) => {
    ev.preventDefault()
    setServerError('')
    if (!validate()) return
    setBusy(true)
    try {
      if (tab === 'login') await login(username.trim(), password)
      else {
        const res = await register(username.trim(), password, nickname.trim(), school.trim())
        // 注册接口可能带回一句面向用户的说明（文案由后端决定），原样展示，
        // 这样前端不必知道那句话具体说了什么。
        if (res?.message) notify(res.message, 'info', 6000)
      }
      // 成功后由上面的 useEffect 统一跳转
    } catch (err) {
      setServerError(err?.message || '操作失败，请稍后重试')
    } finally {
      setBusy(false)
    }
  }

  const notice = serverError || expiredNotice

  return (
    <div className="login-page">
      <div className="login-card card fade-up">
        <div className="login-brand">
          <span className="logo" aria-hidden="true">校</span>
          <div>
            <div className="login-brand-title">校招全流程助手</div>
            <div className="login-brand-sub">登录后，你的简历 · 面试记录 · 报告仅自己可见</div>
          </div>
        </div>

        <div className="login-tabs" role="tablist" aria-label="登录或注册">
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'login'}
            className={`login-tab ${tab === 'login' ? 'on' : ''}`}
            onClick={() => switchTab('login')}
          >
            登录
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'register'}
            className={`login-tab ${tab === 'register' ? 'on' : ''}`}
            onClick={() => switchTab('register')}
          >
            注册
          </button>
        </div>

        {notice && (
          <div className="alert alert--warn login-alert" role="alert">
            <span aria-hidden="true">!</span>
            <span>{notice}</span>
          </div>
        )}

        <form className="login-form" onSubmit={onSubmit} noValidate>
          <div className="field">
            <label className="field-label" htmlFor="login-username">账号</label>
            <div className={`input-wrap ${errors.username ? 'has-error' : ''}`}>
              <span className="input-icon">{Icon.user}</span>
              <input
                id="login-username"
                ref={usernameRef}
                className="input"
                type="text"
                name="username"
                autoComplete="username"
                autoCapitalize="none"
                spellCheck="false"
                placeholder={tab === 'register' ? '3-32 位字母或数字' : '请输入账号'}
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                aria-invalid={errors.username ? 'true' : 'false'}
                aria-describedby={errors.username ? 'login-username-err' : undefined}
                disabled={busy}
              />
            </div>
            {errors.username && (
              <p className="field-error" id="login-username-err" role="alert">{errors.username}</p>
            )}
          </div>

          <div className="field">
            <label className="field-label" htmlFor="login-password">密码</label>
            <div className={`input-wrap ${errors.password ? 'has-error' : ''}`}>
              <span className="input-icon">{Icon.lock}</span>
              <input
                id="login-password"
                ref={passwordRef}
                className="input"
                type={showPwd ? 'text' : 'password'}
                name="password"
                autoComplete={tab === 'login' ? 'current-password' : 'new-password'}
                placeholder={tab === 'register' ? `至少 ${PASSWORD_MIN} 位` : '请输入密码'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                aria-invalid={errors.password ? 'true' : 'false'}
                aria-describedby={errors.password ? 'login-password-err' : undefined}
                disabled={busy}
              />
              <button
                type="button"
                className="pwd-toggle"
                onClick={() => setShowPwd((v) => !v)}
                aria-label={showPwd ? '隐藏密码' : '显示密码'}
                aria-pressed={showPwd}
                tabIndex={-1}
              >
                {showPwd ? Icon.eyeOff : Icon.eye}
              </button>
            </div>
            {errors.password && (
              <p className="field-error" id="login-password-err" role="alert">{errors.password}</p>
            )}
          </div>

          {tab === 'register' && (
            <>
              <div className="field">
                <label className="field-label" htmlFor="login-nickname">
                  昵称 <span className="mute-2">（选填，用于报告与排行榜）</span>
                </label>
                <div className="input-wrap">
                  <input
                    id="login-nickname"
                    className="input"
                    type="text"
                    name="nickname"
                    autoComplete="nickname"
                    maxLength={32}
                    placeholder="如：林宇航"
                    value={nickname}
                    onChange={(e) => setNickname(e.target.value)}
                    disabled={busy}
                  />
                </div>
              </div>

              <div className="field">
                <label className="field-label" htmlFor="login-school">
                  学校 <span className="mute-2">（选填）</span>
                </label>
                <div className="input-wrap">
                  <input
                    id="login-school"
                    className="input"
                    type="text"
                    name="school"
                    autoComplete="organization"
                    maxLength={64}
                    placeholder="如：华南理工大学"
                    value={school}
                    onChange={(e) => setSchool(e.target.value)}
                    disabled={busy}
                  />
                </div>
              </div>
            </>
          )}

          <button className="btn btn--primary btn--block btn--lg login-submit" type="submit" disabled={busy}>
            {busy ? <span className="spinner" /> : null}
            {busy ? '处理中…' : tab === 'login' ? '登录' : '注册并开始'}
          </button>
        </form>

        {allowGuest && (
          <div className="login-guest">
            <span className="mute-2">先不注册？</span>
            <button type="button" className="link-btn" onClick={() => nav('/')}>
              以游客身份体验
            </button>
          </div>
        )}

        <p className="login-note mute-2">
          游客模式下数据会写入公共演示账号，其他访客可见；
          <strong>登录后你的简历、面试记录与报告仅自己可见</strong>。
        </p>

        <p className="login-back">
          <Link to="/" className="link-btn">返回首页</Link>
        </p>
      </div>
    </div>
  )
}
