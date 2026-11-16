import { useEffect, useRef, useState } from 'react'
import usePageVisible from '../hooks/useVisibility'

/**
 * 面试官字幕条（打字机逐字显示 + 光标闪烁）
 *
 * 字幕与语音同步的关键：**显示进度由音频真实播放位置决定，而不是固定毫秒/字**。
 *
 * 旧实现用固定的 34ms/字 推进，而语音是按句子一次性朗读的，两者速度毫无关系：
 * 中文朗读约 7–8 字/秒，34ms/字 的纸面速度看似接近，但标点停顿时 ×3.2、
 * 逗号 ×1.8，实际吞吐大幅下降 —— 结果就是语音已经读完，字幕还停在前半句。
 *
 * 现在每帧读一次 `elapsed()`（音频 currentTime），按
 * 「(已播时长 / 这一句总时长) × 这一句字数」算出此刻该显示到第几个字。
 * 字幕因此是语音的**直接函数**，不存在累积误差：句子读完的同一刻字幕正好读完。
 *
 * props
 *  - timeline: 后端返回的分句时间轴 [{ text, start, end }]
 *  - index:    当前朗读到第几句（useSpeech 按 audio.currentTime 比对得出）
 *  - elapsed:  () => number，返回当前播放位置（秒）；返回 0 表示不在朗读。
 *              用回调而非数值，是为了避免父组件每帧重渲染整个面试页。
 *  - fallback: 没有分句数据时显示的整段文本（speak_text）
 *  - muted:    静音时不打字，整段直接显示
 *  - emptyText: 无内容时的占位文案
 */

/** 没有可用时间轴时的兜底速率：中文正常朗读约 7 字/秒 */
const FALLBACK_CPS = 7

export default function SubtitleBar({
  timeline = [],
  index = 0,
  elapsed,
  fallback = '',
  muted = false,
  emptyText = '点击「开始回答」前，先听面试官提问…',
}) {
  const list = Array.isArray(timeline) && timeline.length ? timeline : []
  const i = list.length ? Math.max(0, Math.min(index, list.length - 1)) : 0

  const current = (list.length ? list[i]?.text : '') || fallback || ''
  const prevText = list.length > 1 && i > 0 ? list[i - 1]?.text || '' : ''
  const { start = 0, end = 0 } = list[i] || {}
  const span = Math.max(0.001, end - start)

  const elapsedRef = useRef(elapsed)
  elapsedRef.current = elapsed
  const rafRef = useRef(0)
  // 页面不可见时没有任何观看者，停掉打字机循环（切回前台由 effect 重新对齐播放位置）
  const visible = usePageVisible()

  // 初始值：静音时整段直出，否则从 0 开始逐字
  const [count, setCount] = useState(() => (muted ? current.length : 0))

  useEffect(() => {
    // 静音、或没有内容：不打字，整段直接显示
    if (muted || !current) {
      setCount(current.length)
      return undefined
    }
    // 页面隐藏：不推进（回到前台 effect 重跑，按真实播放位置一次对齐）
    if (!visible) return undefined

    const total = current.length
    // 没有时间轴信息（span 异常小）时退化为固定速率，保证字幕至少能走完
    const cps = span > 0.15 ? total / span : FALLBACK_CPS

    const tick = () => {
      const pos = typeof elapsedRef.current === 'function' ? elapsedRef.current() : 0

      // pos <= 0：不在朗读（尚未开播 / 已结束）→ 整段显示，不停留在半句
      if (!(pos > 0)) {
        setCount(total)
        return
      }
      // 字幕是播放位置的直接函数，天然不会累积误差
      const ratio = Math.max(0, Math.min(1, (pos - start) / span))
      const next = Math.min(total, Math.max(0, Math.round(total * ratio)))
      setCount((c) => (c === next ? c : next))

      // 这一句已读完：停下，把剩余字符补齐（切句由 index 变化驱动）
      if (ratio >= 1) return
      rafRef.current = requestAnimationFrame(tick)
    }

    // 先用固定速率起跑一帧，避免音频 currentTime 尚未推进时字幕僵住
    const t0 = performance.now()
    const warm = () => {
      const pos = typeof elapsedRef.current === 'function' ? elapsedRef.current() : 0
      if (pos > 0) {
        tick()
        return
      }
      const n = Math.min(total, Math.round((cps * (performance.now() - t0)) / 1000))
      setCount(n)
      if (n < total) rafRef.current = requestAnimationFrame(warm)
    }
    rafRef.current = requestAnimationFrame(warm)

    return () => cancelAnimationFrame(rafRef.current)
    // 只在「句子切换 / 静音 / 文本变化 / 可见性变化」时重建循环；句内推进由循环内部完成
  }, [current, muted, i, start, span, visible])

  const displayed = muted ? current : current.slice(0, Math.min(count, current.length))
  const done = !current || displayed.length >= current.length

  return (
    <div className="subtitle">
      {/* 读屏播报：**只报整句，不报逐字进度**。
          字幕每帧都在变，把 aria-live 挂在打字机本体上会把屏幕阅读器刷屏；
          挂在下面这个 sr-only 节点上，其文本只在换句时改变，因此一句一播报。
          可见的逐字文本照旧留在无障碍树里，手动浏览时仍能读到。 */}
      <p className="sr-only" role="status" aria-live="polite" aria-atomic="true">
        {current}
      </p>
      {/* 上一句是同一内容的回溯展示，重复朗读只会造成干扰 */}
      {prevText && (
        <div className="subtitle-prev" aria-hidden="true">
          {prevText}
        </div>
      )}
      <div className="subtitle-now">
        {current ? displayed : <span className="mute-2">{emptyText}</span>}
        {current && !done && <span className="subtitle-cursor" aria-hidden="true" />}
        {current && done && <span className="subtitle-cursor" style={{ opacity: 0.35 }} aria-hidden="true" />}
      </div>
      {list.length > 1 && (
        <div className="subtitle-dots" title="字幕句进度" aria-hidden="true">
          {list.map((_, n) => (
            <i key={n} className={n <= i ? 'on' : ''} />
          ))}
        </div>
      )}
    </div>
  )
}
