/**
 * 主题控制（体验增强 §13：深色模式）。
 *
 * 三态偏好：`system`（默认，跟随系统）/ `light` / `dark`，存 localStorage。
 *
 * 为什么由 JS 解析 system 而不是写两套 CSS：
 * `@media (prefers-color-scheme: dark)` 与 `[data-theme='dark']` 两套规则会导致
 * 深色令牌重复一份、且用户手动选「浅色」时无法覆盖系统偏好。
 * 这里统一把偏好解析成确定的 `data-theme` 写到 `<html>` 上，
 * CSS 只需一份 `:root[data-theme='dark']`。
 *
 * 防闪烁：index.html 里有一段等价的内联脚本，在首帧之前就把 data-theme 写好，
 * 否则深色用户会先看到一帧白屏。
 */

export const THEME_KEY = 'aic_theme'

/** 三态偏好的展示元信息（顺序即循环顺序） */
export const THEME_OPTIONS = [
  { key: 'system', label: '跟随系统', short: '系统' },
  { key: 'light', label: '浅色', short: '浅色' },
  { key: 'dark', label: '深色', short: '深色' },
]

/** 读取偏好；非法值一律回落到 system */
export function readThemePref() {
  try {
    const raw = localStorage.getItem(THEME_KEY)
    return THEME_OPTIONS.some((o) => o.key === raw) ? raw : 'system'
  } catch {
    return 'system'
  }
}

function systemPrefersDark() {
  try {
    return !!window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches
  } catch {
    return false
  }
}

/** 把偏好解析成实际主题：'light' | 'dark' */
export function resolveTheme(pref) {
  if (pref === 'dark' || pref === 'light') return pref
  return systemPrefersDark() ? 'dark' : 'light'
}

function writeTheme(theme) {
  try {
    document.documentElement.dataset.theme = theme
  } catch {
    /* 忽略 */
  }
}

/** 应用偏好（不持久化），返回实际生效的主题 */
export function applyTheme(pref) {
  const theme = resolveTheme(pref)
  writeTheme(theme)
  return theme
}

/** 保存偏好并立即生效 */
export function setThemePref(pref) {
  try {
    localStorage.setItem(THEME_KEY, pref)
  } catch {
    /* 隐私模式忽略 */
  }
  return applyTheme(pref)
}

/**
 * 启动：应用偏好，并在 system 模式下跟随系统主题变化。
 * 返回取消订阅函数（React 里作为 useEffect 的清理函数）。
 */
export function initTheme() {
  applyTheme(readThemePref())
  let mq = null
  const onChange = () => {
    if (readThemePref() === 'system') applyTheme('system')
  }
  try {
    mq = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null
    if (mq) {
      // addEventListener 在旧 Safari 上不存在，退化为 addListener
      if (mq.addEventListener) mq.addEventListener('change', onChange)
      else if (mq.addListener) mq.addListener(onChange)
    }
  } catch {
    mq = null
  }
  return () => {
    try {
      if (!mq) return
      if (mq.removeEventListener) mq.removeEventListener('change', onChange)
      else if (mq.removeListener) mq.removeListener(onChange)
    } catch {
      /* 忽略 */
    }
  }
}
