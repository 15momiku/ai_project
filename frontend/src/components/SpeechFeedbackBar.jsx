import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { IconBulb, IconMic, IconWarn } from './icons'
import { loadLocal, saveLocal } from '../api/client'
import useSpeechMetrics from '../hooks/useSpeechMetrics'
import '../styles/progress.css'

/**
 * 口播实时反馈条（录音态过渡区）
 *
 * 产品规则（体验增强 §5）：**实时只给事实，评分仍走六维模型**。
 * 所以这里只有 时长 / 语速 / 填充词，永远不出现分数、等级、好/差评价 ——
 * 否则用户会「边答边掉分」，注意力从「把话说清楚」变成「盯着分数」。
 *
 * 三条硬约束：
 *  1. 不弹窗、不打断：一条细线，贴在字幕条上方（录音态过渡区）。
 *  2. 数字最多 ~2 次/秒 更新：提示延迟要求 ≤800ms，但频繁抖动既晃眼、
 *     又会让读屏不停播报，所以统一节流到 500ms 才刷新一次展示值。
 *  3. 纯本地计算：只用 transcript + seconds，**不发任何网络请求**。
 *
 * 存储：不再提示的偏好存在 `loadLocal/saveLocal`（单一稳定 key），
 * 一旦关掉，本机后续所有面试都不再渲染这条（返回 null）。
 *
 * props
 *  - active:     是否正在录音（false 时不渲染）
 *  - transcript: 当前已转写文本
 *  - seconds:    当前题已录制秒数
 *  - onChange:   (metrics) => void，指标变化时回调，父组件上报时带上
 *  - onClose:    () => void，用户点了「不再提示」
 */

/** 「不再提示」偏好：单一稳定 key，只存布尔值 */
export const SPEECH_BAR_HINT_KEY = 'aic_speech_feedback_dismissed'

/** 数字刷新节流：500ms = 2 次/秒（文档要求提示延迟 ≤800ms） */
const PUBLISH_MS = 500

/** 反馈条里不再重复填充词：它已经作为「事实」单独展示在右边 */
const NOTE_SKIP = /填充词\s*\d+\s*次/

/** 语速档位只说事实，不带好/坏评价（也不给分） */
const LEVEL_TEXT = {
  slow: '语速偏慢',
  good: '语速正常',
  fast: '语速偏快',
}

/** 秒 → mm:ss */
function clock(totalSeconds) {
  const s = Math.max(0, Math.floor(Number(totalSeconds) || 0))
  const mm = String(Math.floor(s / 60)).padStart(2, '0')
  const ss = String(s % 60).padStart(2, '0')
  return `${mm}:${ss}`
}

export default function SpeechFeedbackBar({
  active = false,
  transcript = '',
  seconds = 0,
  onChange,
  onClose,
}) {
  // 惰性初始化：已关过就直接不渲染，避免先闪一帧再消失
  const [dismissed, setDismissed] = useState(() => loadLocal(SPEECH_BAR_HINT_KEY, false) === true)
  const metrics = useSpeechMetrics(transcript, seconds)

  // shown = 节流后真正拿去渲染/播报的一份指标快照
  const [shown, setShown] = useState(metrics)

  const lastAt = useRef(0)
  const lastLevel = useRef('')
  const timer = useRef(0)
  const wasActive = useRef(false)
  const metricsRef = useRef(metrics)
  metricsRef.current = metrics

  /* 节流：首次出现、档位变化（值得立刻改口）时立即刷新，其余最多 500ms 一次 */
  useEffect(() => {
    if (!active) return undefined
    const now = Date.now()
    const fresh = metrics.level !== lastLevel.current
    const wait = fresh ? 0 : PUBLISH_MS - (now - lastAt.current)

    if (wait <= 0) {
      timer.current = 0
      lastAt.current = now
      lastLevel.current = metrics.level
      setShown(metrics)
      return undefined
    }
    if (timer.current) return undefined
    timer.current = setTimeout(() => {
      timer.current = 0
      lastAt.current = Date.now()
      lastLevel.current = metricsRef.current.level
      setShown(metricsRef.current)
    }, wait)
    return undefined
  }, [active, metrics])

  /* 录音由开到关：把最后一次口径补上，父组件上报时不会拿到过期数字 */
  useEffect(() => {
    if (wasActive.current && !active) {
      if (timer.current) {
        clearTimeout(timer.current)
        timer.current = 0
      }
      const final = metricsRef.current
      lastAt.current = Date.now()
      lastLevel.current = final.level
      setShown(final)
    }
    wasActive.current = active
  }, [active])

  /* 卸载时清掉挂起的定时器，避免在已卸载组件上 setState */
  useEffect(
    () => () => {
      if (timer.current) {
        clearTimeout(timer.current)
        timer.current = 0
      }
    },
    []
  )

  /* 指标变化回调：父组件在上报时带上（onChange 内部的 setState 不会回流到这里） */
  useEffect(() => {
    if (typeof onChange === 'function') onChange(metrics)
  }, [metrics, onChange])

  const dismiss = useCallback(() => {
    saveLocal(SPEECH_BAR_HINT_KEY, true)
    setDismissed(true)
    if (typeof onClose === 'function') onClose()
  }, [onClose])

  /* 提示词：去掉填充词那半句（右边已作为事实展示），一次只说一句 */
  const hint = useMemo(() => {
    const raw = typeof shown.note === 'string' ? shown.note : ''
    if (!raw) return ''
    return raw
      .split('；')
      .filter((p) => p && !NOTE_SKIP.test(p))
      .join('；')
  }, [shown.note])

  const fillerText = useMemo(() => {
    if (!shown.fillerCount) return ''
    const words = Array.isArray(shown.fillerWords) ? shown.fillerWords : []
    const head = words.slice(0, 3).join('/')
    const more = words.length > 3 ? ' 等' : ''
    return head
      ? `填充词 ${shown.fillerCount} 次「${head}${more}」`
      : `填充词 ${shown.fillerCount} 次`
  }, [shown.fillerCount, shown.fillerWords])

  if (!active || dismissed) return null

  const levelText = LEVEL_TEXT[shown.level] || LEVEL_TEXT.good

  return (
    <div className="sf-bar">
      <IconMic size={15} className="sf-mic" />
      {/* 秒表自己会不停跳：对读屏隐藏，节流后的整句摘要才负责播报 */}
      <span className="sf-clock" aria-hidden="true">
        {clock(shown.durationSeconds ?? seconds)}
      </span>

      <span className="sf-facts">
        <span className="sf-fact">
          {levelText}
          {shown.charsPerMinute > 0 ? <span className="sf-num">{shown.charsPerMinute}</span> : null}
          {shown.charsPerMinute > 0 ? <span className="sf-unit">字/分</span> : null}
        </span>
        {fillerText ? (
          <span className="sf-fact">
            <span className="sf-unit">{fillerText}</span>
          </span>
        ) : null}
      </span>

      {/* 唯一 live region：短句、500ms 才更新一次，不会把读屏刷爆 */}
      <span className="sf-sr" role="status" aria-live="polite" aria-atomic="true">
        {`已录 ${clock(shown.durationSeconds ?? seconds)}，${levelText}${
          shown.charsPerMinute > 0 ? `，每分钟 ${shown.charsPerMinute} 字` : ''
        }${fillerText ? `，${fillerText}` : ''}`}
      </span>

      {hint ? (
        <span className="sf-hint">
          <IconBulb size={13} />
          {hint}
        </span>
      ) : null}

      {shown.focusHint ? (
        <span className="sf-hint">
          <IconWarn size={13} />
          这题已经聊了 {Math.round((shown.durationSeconds || 0) / 60)} 分钟
        </span>
      ) : null}

      <span className="sf-spacer" />

      <button type="button" className="sf-close" onClick={dismiss}>
        不再提示
      </button>
    </div>
  )
}
