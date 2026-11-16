import { useEffect, useRef, useState } from 'react'

/**
 * 页面可见性 + 屏幕常亮（文档 §4 横向规范）
 *
 * 两个钩子解决的是同一件事的两面：**切后台 / 长时间盯屏时的表现**。
 *
 *  - usePageVisible()
 *      订阅 document.visibilityState。纯装饰性动画（眨眼、视线微移、字幕打字机）
 *      在页面隐藏时没有任何观看者，继续跑只会白耗电；切回前台再从头对齐，
 *      比让 setTimeout 在后台堆叠、回来一次性集中触发要稳。
 *      注意：**不要**用它去暂停业务逻辑。录音计时、音频播放位置这类
 *      「用户回到页面时必须仍然正确」的状态，由真实时间/音频位置驱动，天然自愈。
 *
 *  - useWakeLock(active)
 *      屏幕常亮。移动端模拟面试时要念/写 1–2 分钟的长回答，系统息屏会直接
 *      打断作答（录制被切走、语音停摆）。Web 侧只有 Screen Wake Lock API 可用，
 *      而它有明确的失败面：
 *        · 浏览器不支持（Safari < 16.4、部分 WebView）→ supported=false
 *        · 非 HTTPS / 非安全上下文 → request() reject
 *        · 省电模式、后台标签页 → 被浏览器自动释放
 *      所以这里只负责「尽力申请 + 自动重申请」，绝不 throw、绝不阻塞流程；
 *      拿不到就返回 supported/active=false，由调用方给一次温和提示，
 *      引导用户去系统设置里关闭自动锁屏。
 *
 *      关键细节：切到后台时浏览器会**主动释放** wake lock，回到前台必须重新申请，
 *      否则第二题开始屏幕又灭了 —— 这是最常见的「第一题好好的、后面就息屏」原因。
 */

/** 当前页面是否可见（document.hidden 的反相） */
export default function usePageVisible() {
  const [visible, setVisible] = useState(() => {
    if (typeof document === 'undefined') return true
    return document.visibilityState !== 'hidden'
  })

  useEffect(() => {
    if (typeof document === 'undefined') return undefined
    const onChange = () => setVisible(document.visibilityState !== 'hidden')
    // 先同步一次：挂载与首次 visiblechange 之间可能已经切走了
    onChange()
    document.addEventListener('visibilitychange', onChange)
    return () => document.removeEventListener('visibilitychange', onChange)
  }, [])

  return visible
}

/**
 * 申请屏幕常亮。
 * @param {boolean} active 何时需要常亮（例如「面试进行中」）
 * @returns {{supported: boolean, active: boolean}} supported 表示浏览器是否具备能力；
 *          active 表示此刻是否真的持有锁。两者都为 false 时调用方可提示用户。
 */
export function useWakeLock(active) {
  const supported = typeof navigator !== 'undefined' && 'wakeLock' in navigator
  const [held, setHeld] = useState(false)
  const sentinelRef = useRef(null)
  const aliveRef = useRef(true)

  useEffect(() => {
    aliveRef.current = true
    return () => {
      aliveRef.current = false
    }
  }, [])

  useEffect(() => {
    if (!active || !supported || typeof document === 'undefined') {
      setHeld(false)
      return undefined
    }

    let cancelled = false

    const release = () => {
      const s = sentinelRef.current
      sentinelRef.current = null
      setHeld(false)
      // release() 本身也会 reject，且此时报错没有任何可执行动作，直接吞掉
      if (s) Promise.resolve(s.release?.()).catch(() => {})
    }

    const acquire = async () => {
      // 隐藏状态下申请必定失败（规范要求），等 visiblechange 再试
      if (cancelled || document.visibilityState === 'hidden') return
      if (sentinelRef.current) return
      try {
        const s = await navigator.wakeLock.request('screen')
        if (cancelled || !aliveRef.current) {
          Promise.resolve(s.release?.()).catch(() => {})
          return
        }
        sentinelRef.current = s
        setHeld(true)
        // 被浏览器强制释放（省电模式 / 切后台）时同步状态，
        // 回到前台由下面的 visiblechange 重新申请
        s.addEventListener?.('release', () => {
          if (sentinelRef.current === s) {
            sentinelRef.current = null
            setHeld(false)
          }
        })
      } catch {
        // NotAllowedError（省电模式、非安全上下文、权限策略）等：
        // 只是拿不到常亮，不影响面试流程
        if (!cancelled) setHeld(false)
      }
    }

    const onVisible = () => {
      if (document.visibilityState === 'visible') acquire()
      else release()
    }

    acquire()
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      cancelled = true
      document.removeEventListener('visibilitychange', onVisible)
      release()
    }
  }, [active, supported])

  return { supported, active: held }
}
