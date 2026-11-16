import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import {
  authLogin,
  authLogout,
  authMe,
  authRegister,
  authStatus,
  clearToken,
  getToken,
  setToken,
} from '../api/client'

/**
 * 全局登录态。
 *
 * - `user` 为 null 表示还没问到后端（bootstrap 中），不是「未登录」；
 * - 未登录时后端把请求归到公共游客账号，前端据此显示登录引导与「数据不私有」提示；
 * - 任何接口返回 401（令牌过期/被吊销）时，client.js 会清掉本地令牌并广播
 *   `aic-unauthorized`，这里收到后重新 bootstrap —— 于是自动、静默地降级为游客，
 *   而不是让页面停在一堆「请求失败」上。
 */
const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)
  const [allowGuest, setAllowGuest] = useState(true)
  const [expiredNotice, setExpiredNotice] = useState('')
  // 防止 401 广播与 bootstrap 互相触发形成请求风暴
  const booting = useRef(false)

  const refresh = useCallback(async () => {
    try {
      const me = await authMe({ silent: true })
      setUser(me || null)
      return me
    } catch (err) {
      if (err?.status === 401) {
        // client.js 已清掉令牌；再问一次就能拿到游客身份
        try {
          const guest = await authMe({ silent: true })
          setUser(guest || null)
          return guest
        } catch {
          setUser(null)
        }
      } else {
        setUser(null)
      }
      return null
    }
  }, [])

  // 首次挂载：并发问「当前用户」与「是否允许游客」
  useEffect(() => {
    let alive = true
    ;(async () => {
      booting.current = true
      // 必须在发请求**之前**读：401 时 client.js 会把令牌清掉，之后再读就永远是空
      const hadToken = !!getToken()
      const [me, status] = await Promise.all([
        authMe({ silent: true }).catch(() => null),
        authStatus({ silent: true }).catch(() => null),
      ])
      if (!alive) return
      setAllowGuest(status?.allow_guest !== false)

      let current = me
      if (!current) {
        if (hadToken) {
          // 带着令牌却拿不到用户 = 令牌失效，清掉并提示，然后按游客继续
          clearToken()
          setExpiredNotice('登录状态已失效，请重新登录')
        }
        current = await authMe({ silent: true }).catch(() => null)
      }
      if (!alive) return
      setUser(current || null)
      booting.current = false
      setLoading(false)
    })()
    return () => {
      alive = false
    }
  }, [])

  // 任意请求 401 -> 清理并回到游客态
  useEffect(() => {
    const onUnauthorized = (e) => {
      clearToken()
      setExpiredNotice(e?.detail?.message || '登录状态已失效')
      if (!booting.current) refresh()
    }
    window.addEventListener('aic-unauthorized', onUnauthorized)
    return () => window.removeEventListener('aic-unauthorized', onUnauthorized)
  }, [refresh])

  const login = useCallback(async (username, password) => {
    const res = await authLogin(username, password)
    setToken(res.token)
    setUser(res.user)
    setExpiredNotice('')
    return res.user
  }, [])

  const register = useCallback(async (username, password, nickname = '', school = '') => {
    const res = await authRegister(username, password, nickname, school)
    setToken(res.token)
    setUser(res.user)
    setExpiredNotice('')
    // 再补一次完整身份，并返回后端的注册说明文案。
    // 那句文案由后端决定（账号相关提示），前端不各写一份；
    // 调用方（登录页）原样展示给用户即可。
    authMe({ silent: true })
      .then((full) => {
        if (full) setUser(full)
      })
      .catch(() => {})
    return { user: res.user, message: res.message || '' }
  }, [])

  const logout = useCallback(async () => {
    try {
      await authLogout()
    } catch {
      /* 网络失败也要让本地退出，否则用户被困在登录态 */
    }
    clearToken()
    setExpiredNotice('')
    await refresh()
  }, [refresh])

  const isGuest = !user || user.is_guest === true

  const value = useMemo(
    () => ({
      user,
      loading,
      allowGuest,
      isGuest,
      expiredNotice,
      clearExpiredNotice: () => setExpiredNotice(''),
      login,
      register,
      logout,
      // 重新问一次 /auth/me：登录、退出之后都要用它把身份换掉。
      // 任何需要「重新读一次当前身份」的调用方也可以直接用这个出口。
      refresh,
    }),
    [user, loading, allowGuest, isGuest, expiredNotice, login, register, logout, refresh]
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth 必须在 <AuthProvider> 内使用')
  return ctx
}
