import { useEffect, useState } from 'react'
import { IconInfo, IconCheckCircle, IconWarn, IconBulb } from './icons'

/* 类型 → 矢量图标：结构图标不用 emoji，避免各系统渲染不一 */
const TOAST_ICON = {
  error: <IconWarn size={16} />,
  success: <IconCheckCircle size={16} />,
  warn: <IconBulb size={16} />,
  info: <IconInfo size={16} />,
}

/**
 * 全局 Toast：监听 client.js 派发的 `aic-toast` 事件
 * （所有非 2xx 接口失败都会自动弹出，避免白屏无反馈）
 */
export default function Toast() {
  const [items, setItems] = useState([])

  useEffect(() => {
    const onToast = (e) => {
      const { message, type = 'info', duration = 2800 } = e.detail || {}
      if (!message) return
      const id = Date.now() + Math.random()
      setItems((prev) => [...prev.slice(-3), { id, message, type }])
      setTimeout(() => setItems((prev) => prev.filter((t) => t.id !== id)), duration)
    }
    window.addEventListener('aic-toast', onToast)
    return () => window.removeEventListener('aic-toast', onToast)
  }, [])

  if (!items.length) return null

  return (
    <div className="toast-wrap">
      {items.map((t) => (
        <div key={t.id} className={`toast toast--${t.type}`} role="status">
          <span style={{ flex: 'none' }}>
            {TOAST_ICON[t.type] || TOAST_ICON.info}
          </span>
          <span>{t.message}</span>
        </div>
      ))}
    </div>
  )
}
